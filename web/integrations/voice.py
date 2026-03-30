"""Voice AI service: Deepgram (STT) → OpenAI (LLM) → ElevenLabs (TTS)

generate_response() returns (voice_text, send_action) where:
  send_action = None | {"type": str, "content": str}

If send_action is set, the caller should send `content` to the guest via
SMS or WhatsApp (based on the tenant's voice_send_channel setting).
"""

import os
import json
import uuid
import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("VOICE_MOCK_MODE", "true").lower() == "true"

# Detectable send types and the config keys that supply them
SENDABLE_KEYS = {
    "wifi":           "amenities",          # host puts wifi info in amenities
    "location":       "property_city",      # address/city
    "checkin_code":   "custom_instructions",
    "checkin":        "custom_instructions",
    "checkout":       "check_out_time",
    "house_rules":    "house_rules",
    "menu":           "food_menu",
    "restaurants":    "nearby_restaurants",
    "parking":        "parking_policy",
    "faq":            "faq",
}


class VoiceAIService:
    """Orchestrate speech-to-text, LLM response, and text-to-speech."""

    DEEPGRAM_API_KEY      = os.getenv("DEEPGRAM_API_KEY")
    OPENAI_API_KEY        = os.getenv("OPENAI_API_KEY")
    ELEVENLABS_API_KEY    = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID   = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")

    CLOUDFLARE_ACCOUNT_ID       = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    CLOUDFLARE_ACCESS_KEY_ID    = os.getenv("CLOUDFLARE_ACCESS_KEY_ID")
    CLOUDFLARE_SECRET_ACCESS_KEY = os.getenv("CLOUDFLARE_SECRET_ACCESS_KEY")
    CLOUDFLARE_R2_BUCKET        = os.getenv("CLOUDFLARE_R2_BUCKET")

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Speech-to-Text
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def transcribe_audio(audio_url: str) -> tuple[str, float]:
        """
        Transcribe audio from URL using Deepgram STT.
        Returns (transcribed_text, confidence_score).
        In MOCK_MODE returns demo text.
        """
        if MOCK_MODE:
            logger.info(f"[MOCK] Transcribing audio from {audio_url}")
            return "What time can I check in?", 0.95

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                audio_resp = await client.get(audio_url)
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {VoiceAIService.DEEPGRAM_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    params={
                        "model": "nova-2",
                        "detect_language": "true",
                        "punctuate": "true",
                    },
                    content=audio_resp.content,
                )
                if response.status_code == 200:
                    data = response.json()
                    channels = data.get("results", {}).get("channels", [])
                    if channels:
                        alt = channels[0]["alternatives"][0]
                        return alt.get("transcript", ""), alt.get("confidence", 0.8)
                logger.error(f"Deepgram error: {response.status_code} {response.text}")
                return "", 0.0
        except Exception as e:
            logger.error(f"Deepgram transcription error: {e}")
            return "", 0.0

    # ──────────────────────────────────────────────────────────────────────────
    # 2. LLM Response (with send-action detection)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def generate_response(
        guest_message: str,
        tenant_config: dict,
        conversation_history: list[dict],
        guest_name: Optional[str] = None,
    ) -> tuple[str, Optional[dict], Optional[str]]:
        """
        Generate AI response using OpenAI.
        Returns (voice_text, send_action, unanswered_question).

        - send_action = None | {"type": str, "content": str}
          Set when guest asks to have info sent to their phone.
        - unanswered_question = None | str
          Set when the AI genuinely doesn't have the answer.
          Caller should create a VoiceKnowledgeGap and alert the host.

        In MOCK_MODE returns demo values.
        """
        if MOCK_MODE:
            logger.info(f"[MOCK] Generating response for: {guest_message}")
            low = guest_message.lower()
            if any(w in low for w in ("send", "text", "whatsapp", "share", "message")):
                if "wifi" in low or "password" in low:
                    return (
                        "Sure! I'll send the WiFi details to your phone right now.",
                        {"type": "wifi", "content": "📶 WiFi: GuestNetwork\nPassword: Welcome2024"},
                        None,
                    )
                if "location" in low or "address" in low:
                    return (
                        "Sending you the address now!",
                        {"type": "location", "content": "📍 123 Main Street, Beach City\nGoogle Maps: https://maps.google.com"},
                        None,
                    )
                if "code" in low or "pin" in low or "door" in low:
                    return (
                        "I'll send your entry code to your phone.",
                        {"type": "checkin_code", "content": "🔑 Door Code: 4829\nValid for your stay."},
                        None,
                    )
            # Simulate unknown question
            if "pool" in low or "gym" in low or "spa" in low:
                return (
                    "I'm sorry, I don't have that information yet. I'll let the host know you asked — they'll get back to you shortly.",
                    None,
                    guest_message,
                )
            return "Check-in is at 3 PM. You can enter using code 1234 at the main door.", None, None

        try:
            # Build property context block
            cfg = tenant_config
            address = cfg.get("property_city", "")
            amenities = cfg.get("amenities", "")
            faq_text = cfg.get("faq", "")
            food_menu = cfg.get("food_menu", "")
            nearby = cfg.get("nearby_restaurants", "")
            parking = cfg.get("parking_policy", "")
            house_rules = cfg.get("house_rules", "")
            custom_instructions = cfg.get("custom_instructions", "")

            # Build a compact sendable-info block so the LLM knows what to attach
            sendable_info = {}
            if amenities:
                sendable_info["wifi"] = amenities
            if address:
                sendable_info["location"] = address
            if custom_instructions:
                sendable_info["checkin"] = custom_instructions[:500]
            if house_rules:
                sendable_info["house_rules"] = house_rules[:400]
            if food_menu:
                sendable_info["menu"] = food_menu[:400]
            if nearby:
                sendable_info["restaurants"] = nearby[:400]
            if parking:
                sendable_info["parking"] = parking[:300]
            if faq_text:
                sendable_info["faq"] = faq_text[:600]

            guest_label = f"Guest: {guest_name}" if guest_name else "Guest"

            system_prompt = f"""You are a helpful AI concierge answering phone calls for a property.

PROPERTY INFO:
- Type: {cfg.get('property_type', 'property')}
- City/Address: {address}
- Check-in: {cfg.get('check_in_time', '15:00')} | Check-out: {cfg.get('check_out_time', '11:00')}
- Max guests: {cfg.get('max_guests', 'N/A')}
- Quiet hours: {cfg.get('quiet_hours', 'N/A')}
- Amenities: {amenities[:300] if amenities else 'N/A'}
- House rules: {house_rules[:300] if house_rules else 'Standard rules apply'}
- Pet policy: {cfg.get('pet_policy', 'N/A')}
- Parking: {parking[:200] if parking else 'N/A'}
- FAQ: {faq_text[:500] if faq_text else ''}
- Custom instructions: {custom_instructions[:300] if custom_instructions else ''}

{guest_label} is on the phone.

SENDABLE INFO (what you can text/WhatsApp to the guest during this call):
{json.dumps(sendable_info, ensure_ascii=False)}

RULES:
1. Keep voice replies under 80 words — natural, conversational, no bullet points.
2. If the guest asks you to "send", "text", "WhatsApp", "share", or "message" any specific info, set send.content with the relevant details formatted nicely with emojis.
3. If nothing should be sent, set send to null.
4. Respond in the same language the guest is speaking.
5. IMPORTANT: If the guest asks a specific question you genuinely cannot answer from the property info above, set unknown to true and unanswered_question to exactly what the guest asked. Tell them: "I don't have that info right now, but I'll make sure the host is aware and can update their listing."
6. Only set unknown=true for real knowledge gaps (missing facts about the property). Do NOT set it for greetings, chit-chat, or questions you can answer.

RESPONSE FORMAT — always respond with valid JSON only, no markdown:
{{"voice": "<what you say out loud>", "send": null, "unknown": false, "unanswered_question": null}}
or when sending info:
{{"voice": "<what you say>", "send": {{"type": "<wifi|location|checkin|checkout|house_rules|menu|restaurants|parking|faq>", "content": "<formatted text>"}}, "unknown": false, "unanswered_question": null}}
or when you don't know:
{{"voice": "I don't have that info right now, but I'll let the host know so they can update their listing.", "send": null, "unknown": true, "unanswered_question": "<the specific question the guest asked>"}}"""

            messages = []
            for i, msg in enumerate(conversation_history[-6:]):
                role = "user" if i % 2 == 0 else "assistant"
                messages.append({"role": role, "content": msg["text"]})
            messages.append({"role": "user", "content": guest_message})

            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {VoiceAIService.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [{"role": "system", "content": system_prompt}] + messages,
                        "temperature": 0.7,
                        "max_tokens": 300,
                        "response_format": {"type": "json_object"},
                    },
                )

            if resp.status_code != 200:
                logger.error(f"OpenAI error: {resp.status_code} {resp.text}")
                return "Sorry, I couldn't understand that. Could you repeat?", None

            raw = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(raw)
            voice_text          = data.get("voice", "Sorry, I couldn't process that.")
            send_action         = data.get("send")          # None | {"type", "content"}
            unanswered_question = data.get("unanswered_question") if data.get("unknown") else None
            return voice_text, send_action, unanswered_question

        except json.JSONDecodeError:
            try:
                return raw, None, None  # type: ignore[name-defined]
            except Exception:
                return "Sorry, I couldn't understand that. Could you repeat?", None, None
        except Exception as e:
            logger.error(f"OpenAI generation error: {e}")
            return "Sorry, I couldn't understand that. Could you repeat?", None, None

        # Fallback (should never reach here but ensures 3-tuple always)
        return "I'm having trouble processing that right now.", None, None

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Text-to-Speech
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def synthesize_speech(text: str, voice_id: Optional[str] = None) -> tuple[bytes, str]:
        """
        Convert text to speech using ElevenLabs.
        Returns (audio_bytes, audio_url).
        In MOCK_MODE returns dummy bytes and mock URL.
        voice_id: optional voice ID (defaults to class ELEVENLABS_VOICE_ID)
        """
        if MOCK_MODE:
            logger.info(f"[MOCK] Synthesizing speech: {text[:60]}")
            dummy_mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
            mock_url = f"https://mock-r2.example.com/voice_{uuid.uuid4()}.mp3"
            return dummy_mp3, mock_url

        try:
            vid = voice_id or VoiceAIService.ELEVENLABS_VOICE_ID
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                    headers={
                        "xi-api-key": VoiceAIService.ELEVENLABS_API_KEY,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": text,
                        "model_id": "eleven_turbo_v2",
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                    },
                )
                if response.status_code == 200:
                    audio_bytes = response.content
                    url = await VoiceAIService.upload_to_r2(audio_bytes, f"voice_{uuid.uuid4()}.mp3")
                    return audio_bytes, url
                logger.error(f"ElevenLabs error: {response.status_code} {response.text}")
                return b"", ""
        except Exception as e:
            logger.error(f"ElevenLabs TTS error: {e}")
            return b"", ""

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Storage — Cloudflare R2
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def upload_to_r2(file_data: bytes, file_name: str) -> str:
        """Upload audio to Cloudflare R2 and return public URL."""
        if MOCK_MODE:
            logger.info(f"[MOCK] Uploading to R2: {file_name}")
            return f"https://mock-r2.example.com/{file_name}"

        try:
            import boto3

            r2_client = boto3.client(
                "s3",
                region_name="auto",
                endpoint_url=f"https://{VoiceAIService.CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com",
                aws_access_key_id=VoiceAIService.CLOUDFLARE_ACCESS_KEY_ID,
                aws_secret_access_key=VoiceAIService.CLOUDFLARE_SECRET_ACCESS_KEY,
            )
            key = f"calls/{file_name}"
            r2_client.put_object(
                Bucket=VoiceAIService.CLOUDFLARE_R2_BUCKET,
                Key=key,
                Body=file_data,
                ContentType="audio/mpeg",
            )
            url = (
                f"https://{VoiceAIService.CLOUDFLARE_R2_BUCKET}"
                f".{VoiceAIService.CLOUDFLARE_ACCOUNT_ID}"
                f".r2.cloudflarestorage.com/{key}"
            )
            logger.info(f"[R2] Uploaded: {url}")
            return url
        except Exception as e:
            logger.error(f"R2 upload error: {e}")
            return ""

    # Keep old alias
    @staticmethod
    async def upload_to_s3(file_data: bytes, file_name: str) -> str:
        return await VoiceAIService.upload_to_r2(file_data, file_name)

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Sentiment analysis (post-call)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def analyze_sentiment(transcript: str) -> str:
        """
        Classify call transcript sentiment.
        Returns 'positive', 'neutral', or 'negative'.
        """
        if MOCK_MODE:
            return "positive"
        if not transcript or not VoiceAIService.OPENAI_API_KEY:
            return "neutral"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {VoiceAIService.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o-mini",
                        "messages": [
                            {
                                "role": "system",
                                "content": "Classify the sentiment of this guest-AI call transcript as exactly one word: positive, neutral, or negative.",
                            },
                            {"role": "user", "content": transcript[:1000]},
                        ],
                        "max_tokens": 5,
                        "temperature": 0,
                    },
                )
            if resp.status_code == 200:
                word = resp.json()["choices"][0]["message"]["content"].strip().lower()
                if word in ("positive", "negative"):
                    return word
            return "neutral"
        except Exception as e:
            logger.error(f"Sentiment analysis error: {e}")
            return "neutral"
