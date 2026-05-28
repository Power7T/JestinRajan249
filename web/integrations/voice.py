"""Voice AI service: Deepgram (STT) → OpenAI (LLM) → Google TTS / ElevenLabs (TTS)

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

    DEEPGRAM_API_KEY        = os.getenv("DEEPGRAM_API_KEY")
    DEEPGRAM_MODEL          = os.getenv("DEEPGRAM_MODEL", "nova-2")
    OPENAI_API_KEY          = os.getenv("OPENAI_API_KEY")
    OPENROUTER_API_KEY      = os.getenv("OPENROUTER_API_KEY")
    LLM_MODEL               = os.getenv("LLM_MODEL", "openai/gpt-4o-mini")
    ELEVENLABS_API_KEY      = os.getenv("ELEVENLABS_API_KEY")
    ELEVENLABS_VOICE_ID     = os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
    ELEVENLABS_MODEL        = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2")
    ELEVENLABS_STABILITY    = float(os.getenv("ELEVENLABS_STABILITY", "0.5"))
    ELEVENLABS_SIMILARITY   = float(os.getenv("ELEVENLABS_SIMILARITY", "0.75"))

    # Google Cloud TTS
    GOOGLE_TTS_API_KEY      = os.getenv("GOOGLE_TTS_API_KEY")
    GOOGLE_TTS_VOICE        = os.getenv("GOOGLE_TTS_VOICE", "en-US-Neural2-F")
    GOOGLE_TTS_LANGUAGE     = os.getenv("GOOGLE_TTS_LANGUAGE", "en-US")
    GOOGLE_TTS_SPEAKING_RATE = float(os.getenv("GOOGLE_TTS_SPEAKING_RATE", "1.0"))
    TTS_PROVIDER            = os.getenv("TTS_PROVIDER", "google")  # "google" | "elevenlabs"

    CLOUDFLARE_ACCOUNT_ID       = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    CLOUDFLARE_ACCESS_KEY_ID    = os.getenv("CLOUDFLARE_ACCESS_KEY_ID")
    CLOUDFLARE_SECRET_ACCESS_KEY = os.getenv("CLOUDFLARE_SECRET_ACCESS_KEY")
    CLOUDFLARE_R2_BUCKET        = os.getenv("CLOUDFLARE_R2_BUCKET")

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Speech-to-Text
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def transcribe_bytes(audio_bytes: bytes) -> tuple[str, float]:
        """Send raw audio bytes directly to Deepgram — no file upload needed."""
        import asyncio

        async def _call():
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {VoiceAIService.DEEPGRAM_API_KEY}",
                        "Content-Type": "audio/webm",
                    },
                    params={
                        "model": VoiceAIService.DEEPGRAM_MODEL,
                        "detect_language": "true",
                        "punctuate": "true",
                    },
                    content=audio_bytes,
                )
                if response.status_code == 200:
                    data = response.json()
                    channels = data.get("results", {}).get("channels", [])
                    if channels:
                        alt = channels[0]["alternatives"][0]
                        return alt.get("transcript", ""), alt.get("confidence", 0.8)
                logger.error(f"Deepgram error: {response.status_code} {response.text[:200]}")
                return "", 0.0

        try:
            return await asyncio.wait_for(_call(), timeout=8.0)
        except asyncio.TimeoutError:
            logger.error("Deepgram transcribe_bytes timeout")
            return "", 0.0
        except Exception as e:
            logger.error(f"Deepgram transcribe_bytes error: {e}")
            return "", 0.0

    @staticmethod
    async def transcribe_audio(audio_url: str) -> tuple[str, float]:
        """
        Transcribe audio from URL using Deepgram STT with timeout protection.
        Returns (transcribed_text, confidence_score).

        Timeout: 8 seconds (Deepgram should respond within this)
        Fallback on timeout: ("", 0.0) — guest message marked as "[audio unclear]"
        """
        import asyncio

        async def _call_deepgram():
            async with httpx.AsyncClient(timeout=30) as client:
                audio_resp = await client.get(audio_url)
                response = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={
                        "Authorization": f"Token {VoiceAIService.DEEPGRAM_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    params={
                        "model": VoiceAIService.DEEPGRAM_MODEL,
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

        try:
            # Hard timeout: 8 seconds for Deepgram
            result = await asyncio.wait_for(_call_deepgram(), timeout=8.0)
            return result
        except asyncio.TimeoutError:
            logger.error(f"[TIMEOUT] Deepgram transcription exceeded 8s timeout")
            return "", 0.0  # Fallback: no transcription
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
        guest_language: str = "en",
    ) -> tuple[str, Optional[dict], Optional[str]]:
        """
        Generate AI response using OpenAI.
        Returns (voice_text, send_action, unanswered_question).

        - send_action = None | {"type": str, "content": str}
          Set when guest asks to have info sent to their phone.
        - unanswered_question = None | str
          Set when the AI genuinely doesn't have the answer.
          Caller should create a VoiceKnowledgeGap and alert the host.
        - guest_language: language code (e.g., 'en', 'es', 'fr', 'de', 'zh', 'ja')
        """
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

            # Language instruction
            lang_instruction = ""
            if guest_language != "en":
                lang_names = {
                    "es": "Spanish",
                    "fr": "French",
                    "de": "German",
                    "zh": "Mandarin Chinese",
                    "ja": "Japanese",
                    "pt": "Portuguese",
                    "it": "Italian",
                    "nl": "Dutch",
                }
                lang_name = lang_names.get(guest_language, "the guest's language")
                lang_instruction = f"\n⚠️ IMPORTANT: The guest is speaking {lang_name}. Respond ENTIRELY in {lang_name}, not English. Translate your entire response.\n"

            # Guest-specific context
            guest_room = cfg.get('guest_room', '')
            guest_property = cfg.get('guest_property', '')
            guest_reservation = cfg.get('guest_reservation', '')

            guest_info_section = ""
            if guest_room or guest_property or guest_reservation:
                guest_info_section = "\nGUEST INFO:"
                if guest_room:
                    guest_info_section += f"\n- Room/Unit: {guest_room}"
                if guest_property:
                    guest_info_section += f"\n- Property: {guest_property}"
                if guest_reservation:
                    guest_info_section += f"\n- Stay: {guest_reservation}"

            system_prompt = f"""You are a helpful AI concierge answering phone calls for a property.{lang_instruction}
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
- Custom instructions: {custom_instructions[:300] if custom_instructions else ''}{guest_info_section}

{guest_label} is on the phone.

SENDABLE INFO (what you can text/WhatsApp to the guest during this call):
{json.dumps(sendable_info, ensure_ascii=False)}

AGENTIC ACTIONS — these are things you CAN DO directly. When a guest asks for any
of these, respond confidently that you will handle it and set action_type accordingly.
NEVER say "I don't have that info" or "I'll let the host know" for these — they are
actions you execute, not information you look up:
  • Late checkout (staying past checkout TIME, same day) → action_type: "late_checkout"
    Say: "Absolutely! I'm arranging a late checkout for you now."
  • Early check-in (arriving before check-in time) → action_type: "early_checkin"
    Say: "Let me sort that early check-in for you right away."
  • Extending stay / staying extra nights / later checkout DATE → action_type: "extend_stay"
    Say: "I'll process that extension for you now."
  • Adding an extra guest → action_type: "extra_guest"
    Say: "I'll update your reservation to add the extra guest."
  • Logging a special request or note → action_type: "add_note"
    Say: "Got it — I've noted that for you."

CALL END — if the guest says goodbye, ends the call, thanks and hangs up, or says
"end the call", "bye", "that's all", "hang up", etc.:
  Set end_call to true. Give a warm closing line (under 15 words). Do NOT keep asking
  how you can help after the guest has clearly said goodbye.

RULES:
1. Keep voice replies under 80 words — natural, conversational, no bullet points.
2. If the guest asks you to "send", "text", "WhatsApp", "share", or "message" any specific info, set send.content with the relevant details formatted nicely with emojis.
3. If nothing should be sent, set send to null.
4. Respond in the same language the guest is speaking.
5. If the guest mentions wanting a callback ("call me back", "ring me later", etc.), acknowledge and say you'll arrange it.
6. When referencing property amenities or rules, personalize to the guest's room/unit if known.
7. IMPORTANT: Only set unknown=true when the guest asks about a specific fact about the property that is genuinely missing from the property info above (e.g. the pool opening hours when no hours are listed). Do NOT set unknown=true for: agentic actions (late checkout etc.), greetings, chit-chat, general questions you can answer, or anything in the AGENTIC ACTIONS section above.

RESPONSE FORMAT — always respond with valid JSON only, no markdown:
Standard reply:
{{"voice": "<what you say>", "send": null, "unknown": false, "unanswered_question": null, "action_type": null, "end_call": false}}

When sending info to guest's phone:
{{"voice": "<what you say>", "send": {{"type": "<wifi|location|checkin|checkout|house_rules|menu|restaurants|parking|faq>", "content": "<formatted text>"}}, "unknown": false, "unanswered_question": null, "action_type": null, "end_call": false}}

When taking a reservation action — IMPORTANT use this for ANY request to extend stay, late checkout, early checkin, add guest:
{{"voice": "Absolutely! I'm taking care of that for you right now.", "send": null, "unknown": false, "unanswered_question": null, "action_type": "late_checkout", "end_call": false}}

When ending the call:
{{"voice": "Thank you for calling! Have a wonderful stay.", "send": null, "unknown": false, "unanswered_question": null, "action_type": null, "end_call": true}}

When genuinely missing property info:
{{"voice": "I don't have that detail right now, but I'll make sure the host is aware.", "send": null, "unknown": true, "unanswered_question": "<exact question>", "action_type": null, "end_call": false}}"""

            import asyncio

            messages = []
            for msg in conversation_history[-10:]:
                role = msg.get("role", "user")
                content = msg.get("content") or msg.get("text", "")
                if content:
                    messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": guest_message})

            async def _call_llm():
                # Use OpenRouter if available, otherwise fallback to OpenAI
                if VoiceAIService.OPENROUTER_API_KEY:
                    # OpenRouter call
                    async with httpx.AsyncClient(timeout=20) as client:
                        resp = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers={
                                "Authorization": f"Bearer {VoiceAIService.OPENROUTER_API_KEY}",
                                "Content-Type": "application/json",
                                "X-OpenRouter-Title": "HostAI Voice",
                            },
                            json={
                                "model": VoiceAIService.LLM_MODEL,
                                "messages": [{"role": "system", "content": system_prompt}] + messages,
                                "temperature": 0.7,
                                "max_tokens": 300,
                                "response_format": {"type": "json_object"},
                            },
                        )

                    if resp.status_code != 200:
                        logger.error(f"OpenRouter error: {resp.status_code} {resp.text}")
                        return None

                else:
                    # OpenAI fallback
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
                        return None

                raw = resp.json()["choices"][0]["message"]["content"]
                data = json.loads(raw)
                voice_text          = data.get("voice", "Sorry, I couldn't process that.")
                send_action         = data.get("send")          # None | {"type", "content"}
                unanswered_question = data.get("unanswered_question") if data.get("unknown") else None
                action_type         = data.get("action_type")  # None | "late_checkout" | etc.
                end_call            = bool(data.get("end_call", False))
                # Pack action_type and end_call into send_action so callers get them
                if action_type or end_call:
                    send_action = send_action or {}
                    if isinstance(send_action, dict):
                        if action_type:
                            send_action["action_type"] = action_type
                        if end_call:
                            send_action["end_call"] = True
                return (voice_text, send_action, unanswered_question)

            try:
                # Hard timeout: 6 seconds for LLM call
                result = await asyncio.wait_for(_call_llm(), timeout=6.0)
                if result:
                    return result
                return "Sorry, I couldn't understand that. Could you repeat?", None, None
            except asyncio.TimeoutError:
                logger.error(f"[TIMEOUT] LLM generation exceeded 6s timeout")
                return "Sorry, I'm having trouble understanding. Could you repeat that?", None, None

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
        Convert text to speech.
        Routes to Google Cloud TTS (default) or ElevenLabs based on TTS_PROVIDER.
        Returns (audio_bytes, audio_url).
        Fallback: if primary fails, tries the other provider.
        """
        import asyncio

        provider = VoiceAIService.TTS_PROVIDER or "google"

        if provider == "google":
            result = await VoiceAIService._synthesize_google(text)
            if result[0]:
                return result
            # Fallback to ElevenLabs if Google fails
            logger.warning("Google TTS failed, falling back to ElevenLabs")
            return await VoiceAIService._synthesize_elevenlabs(text, voice_id)
        else:
            result = await VoiceAIService._synthesize_elevenlabs(text, voice_id)
            if result[0]:
                return result
            # Fallback to Google if ElevenLabs fails
            logger.warning("ElevenLabs TTS failed, falling back to Google TTS")
            return await VoiceAIService._synthesize_google(text)

    @staticmethod
    async def _synthesize_google(text: str) -> tuple[bytes, str]:
        """Google Cloud TTS — Neural2 voices, unlimited concurrency, ~$1.60/mo at scale."""
        import asyncio
        import base64

        async def _call():
            api_key = VoiceAIService.GOOGLE_TTS_API_KEY
            if not api_key:
                logger.error("Google TTS: no API key configured")
                return b"", ""
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}",
                    json={
                        "input": {"text": text},
                        "voice": {
                            "languageCode": VoiceAIService.GOOGLE_TTS_LANGUAGE,
                            "name": VoiceAIService.GOOGLE_TTS_VOICE,
                        },
                        "audioConfig": {
                            "audioEncoding": "MP3",
                            "speakingRate": VoiceAIService.GOOGLE_TTS_SPEAKING_RATE,
                        },
                    },
                )
                if resp.status_code == 200:
                    audio_bytes = base64.b64decode(resp.json()["audioContent"])
                    import asyncio as _asyncio
                    if VoiceAIService.CLOUDFLARE_ACCOUNT_ID and VoiceAIService.CLOUDFLARE_R2_BUCKET:
                        _asyncio.create_task(
                            VoiceAIService.upload_to_r2(audio_bytes, f"voice_{uuid.uuid4()}.mp3")
                        )
                    return audio_bytes, ""
                logger.error(f"Google TTS error: {resp.status_code} {resp.text[:200]}")
                return b"", ""

        try:
            return await asyncio.wait_for(_call(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.error("[TIMEOUT] Google TTS exceeded 10s")
            return b"", ""
        except Exception as e:
            logger.error(f"Google TTS error: {e}")
            return b"", ""

    @staticmethod
    async def _synthesize_elevenlabs(text: str, voice_id: Optional[str] = None) -> tuple[bytes, str]:
        """ElevenLabs TTS — optional premium voices."""
        import asyncio

        async def _call():
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
                        "model_id": VoiceAIService.ELEVENLABS_MODEL,
                        "voice_settings": {
                            "stability": VoiceAIService.ELEVENLABS_STABILITY,
                            "similarity_boost": VoiceAIService.ELEVENLABS_SIMILARITY,
                        },
                    },
                )
                if response.status_code == 200:
                    audio_bytes = response.content
                    import asyncio as _asyncio
                    if VoiceAIService.CLOUDFLARE_ACCOUNT_ID and VoiceAIService.CLOUDFLARE_R2_BUCKET:
                        _asyncio.create_task(
                            VoiceAIService.upload_to_r2(audio_bytes, f"voice_{uuid.uuid4()}.mp3")
                        )
                    return audio_bytes, ""
                logger.error(f"ElevenLabs error: {response.status_code} {response.text}")
                return b"", ""

        try:
            return await asyncio.wait_for(_call(), timeout=15.0)
        except asyncio.TimeoutError:
            logger.error("[TIMEOUT] ElevenLabs TTS exceeded 15s")
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
