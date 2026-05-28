# © 2024 Jestin Rajan. All rights reserved.
"""
Streaming Voice via Twilio Media Streams.

Replaces the Gather-based turn-based flow with real-time bidirectional audio:
- Twilio streams 8kHz μ-law audio chunks over WebSocket
- Server streams to Deepgram for live transcription
- LLM generates response (can interrupt the guest)
- ElevenLabs / Google TTS produces audio
- Audio chunks streamed back to Twilio in real-time

Latency target: ~600-1000ms (vs ~3s for Gather).
"""
from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Twilio Media Streams send μ-law audio at 8 kHz, 20 ms frames (160 bytes each)
_TWILIO_SAMPLE_RATE = 8000
_TWILIO_FRAME_BYTES = 160

# Heuristic VAD: how many consecutive low-energy frames count as "silence"
# 20 ms × 25 frames = 500 ms of silence → assume guest finished speaking
_SILENCE_FRAMES_THRESHOLD = 25
# Minimum audio before considering a turn complete (avoid micro-noise triggers)
_MIN_AUDIO_FRAMES = 30  # 600 ms

# Average μ-law energy for silence ≈ 0xFF (255) — speech is much lower in magnitude
_SILENCE_BYTE = 0xFF
_SILENCE_TOLERANCE = 5  # bytes within this of 0xFF count as silence


def _is_silence_frame(mu_law: bytes) -> bool:
    """Cheap energy check: count bytes near the μ-law silence value."""
    if not mu_law:
        return True
    near_silent = sum(1 for b in mu_law if abs(b - _SILENCE_BYTE) <= _SILENCE_TOLERANCE)
    return near_silent > len(mu_law) * 0.85


class VoiceStreamSession:
    """One per active call. Manages audio buffers, STT, LLM, TTS streaming."""

    def __init__(self, call_id: str, stream_sid: str, websocket):
        self.call_id      = call_id
        self.stream_sid   = stream_sid
        self.websocket    = websocket
        self.audio_buffer = bytearray()
        self.silence_frames = 0
        self.audio_frames   = 0
        self.is_speaking    = False
        self.is_responding  = False
        self.call_ended     = False   # set True when AI detects goodbye
        self.conversation_history: list[dict] = []
        self.tenant_id: Optional[str] = None
        self.cfg = None
        self.tenant_config_dict: dict = {}
        self.guest_name: Optional[str] = None

    # ── Init tenant context from the VoiceCall record ─────────────────────

    async def initialize(self):
        """Look up the VoiceCall + tenant config for prompt context."""
        try:
            from web.db import SessionLocal
            from web.models import VoiceCall, TenantConfig
            with SessionLocal() as db:
                voice_call = db.query(VoiceCall).filter_by(id=self.call_id).first()
                if not voice_call:
                    log.warning("[STREAM] VoiceCall %s not found", self.call_id)
                    return
                self.tenant_id = voice_call.tenant_id
                cfg = db.query(TenantConfig).filter_by(tenant_id=self.tenant_id).first()
                if cfg:
                    self.cfg = cfg
                    self.tenant_config_dict = {
                        "property_type":        cfg.property_type        or "property",
                        "property_city":        cfg.property_city        or "",
                        "check_in_time":        cfg.check_in_time        or "15:00",
                        "check_out_time":       cfg.check_out_time       or "11:00",
                        "house_rules":          (cfg.house_rules or "")[:500],
                        "faq":                  (cfg.faq or "")[:500],
                        "custom_instructions":  (cfg.custom_instructions or "")[:500],
                        "amenities":            (cfg.amenities or "")[:200],
                        "nearby_restaurants":   (cfg.nearby_restaurants or "")[:200],
                    }
        except Exception as exc:
            log.error("[STREAM] initialize() failed: %s", exc, exc_info=True)

    # ── Incoming audio handler ────────────────────────────────────────────

    async def on_media(self, payload_b64: str):
        """Called when Twilio sends a 20 ms audio frame."""
        if self.call_ended:
            return  # ignore audio after AI has said goodbye
        if self.is_responding:
            # AI is currently speaking — track barge-in attempts (interrupt detection)
            return

        try:
            audio = base64.b64decode(payload_b64)
        except Exception:
            return

        # Append to rolling buffer
        self.audio_buffer.extend(audio)
        self.audio_frames += 1

        # VAD: speaking vs silent
        if _is_silence_frame(audio):
            self.silence_frames += 1
        else:
            self.silence_frames = 0
            self.is_speaking = True

        # Detect end-of-utterance: was speaking, then sustained silence
        if (self.is_speaking
                and self.audio_frames >= _MIN_AUDIO_FRAMES
                and self.silence_frames >= _SILENCE_FRAMES_THRESHOLD):
            buffered = bytes(self.audio_buffer)
            self.audio_buffer.clear()
            self.silence_frames = 0
            self.audio_frames   = 0
            self.is_speaking    = False
            asyncio.create_task(self._process_turn(buffered))

    async def on_start(self, params: dict):
        """Twilio start event."""
        log.info("[STREAM] start call_id=%s stream_sid=%s", self.call_id, self.stream_sid)
        await self.initialize()
        # Send greeting
        greeting = "Hello! How can I help you today?"
        if self.cfg and self.cfg.property_names:
            prop = (self.cfg.property_names or "").split(",")[0].strip()
            if prop:
                greeting = f"Hello, welcome to {prop}. How can I help you today?"
        await self._speak(greeting)

    async def on_stop(self):
        log.info("[STREAM] stop call_id=%s frames_processed=%d", self.call_id, self.audio_frames)

    # ── Pipeline: STT → LLM → TTS ─────────────────────────────────────────

    async def _process_turn(self, mu_law_audio: bytes):
        """One full turn: transcribe → generate → speak."""
        if not mu_law_audio:
            return
        try:
            self.is_responding = True

            # 1) Convert μ-law → PCM16 → WAV bytes (for Deepgram)
            try:
                pcm16 = audioop.ulaw2lin(mu_law_audio, 2)
            except Exception as exc:
                log.error("[STREAM] μ-law decode failed: %s", exc)
                self.is_responding = False
                return

            transcript, confidence = await self._stt(pcm16)
            if not transcript:
                self.is_responding = False
                return
            log.info("[STREAM] [%s] guest: %s (conf=%.2f)", self.call_id, transcript[:80], confidence)
            self.conversation_history.append({"text": transcript, "ts": datetime.now(timezone.utc).isoformat()})

            # 2) LLM response
            response_text, action_type, end_call = await self._llm(transcript)
            if not response_text:
                self.is_responding = False
                return
            log.info("[STREAM] [%s] AI: %s (end_call=%s)", self.call_id, response_text[:80], end_call)

            # 3) TTS — speak initial acknowledgement immediately so guest isn't left waiting
            await self._speak(response_text)

            # 4) Execute PMS action and speak the REAL result back (sequential, not background)
            #    e.g. "Great news — your checkout is extended to 2pm!"
            #    or   "Your unit is taken, but Unit B is available at £120/night..."
            if action_type and self.tenant_id:
                await self._execute_voice_action(transcript, action_type)

            # 5) End the call if AI said goodbye
            if end_call:
                self.call_ended = True
                log.info("[STREAM] [%s] AI ended the call gracefully", self.call_id)
                # Send Twilio stop event to hang up
                await self._hangup()
        except Exception as exc:
            log.error("[STREAM] _process_turn error: %s", exc, exc_info=True)
        finally:
            self.is_responding = False

    async def _stt(self, pcm16_audio: bytes) -> tuple[str, float]:
        """Transcribe a chunk of PCM16 audio. Uses Deepgram if configured."""
        try:
            from web.integrations.voice import VoiceAIService
            import wave
            import io as _io
            # Wrap PCM16 in a WAV header for Deepgram
            buf = _io.BytesIO()
            with wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(_TWILIO_SAMPLE_RATE)
                wav.writeframes(pcm16_audio)
            wav_bytes = buf.getvalue()
            # VoiceAIService.transcribe_audio takes a URL; we have raw bytes — try the bytes variant if available
            if hasattr(VoiceAIService, "transcribe_audio_bytes"):
                return await VoiceAIService.transcribe_audio_bytes(wav_bytes)
            # Fallback: upload to R2/S3, then transcribe
            from uuid import uuid4
            url = await VoiceAIService.upload_to_r2(wav_bytes, f"stream-{uuid4()}.wav") if hasattr(VoiceAIService, "upload_to_r2") else None
            if url:
                return await VoiceAIService.transcribe_audio(url)
            return ("", 0.0)
        except Exception as exc:
            log.warning("[STREAM] STT failed: %s", exc)
            return ("", 0.0)

    async def _llm(self, transcript: str) -> tuple[str, Optional[str], bool]:
        """Run the LLM to generate a reply. Returns (text, action_type, end_call)."""
        try:
            from web.integrations.voice import VoiceAIService
            ai_text, send_action, _gap = await VoiceAIService.generate_response(
                transcript,
                self.tenant_config_dict,
                self.conversation_history,
                guest_name=self.guest_name,
            )
            if ai_text:
                self.conversation_history.append({"role": "assistant", "text": ai_text})
            action_type = None
            end_call = False
            if isinstance(send_action, dict):
                action_type = send_action.get("action_type")
                end_call = bool(send_action.get("end_call", False))
            return (ai_text or "").strip(), action_type, end_call
        except Exception as exc:
            log.warning("[STREAM] LLM failed: %s", exc)
            return "", None, False

    async def _hangup(self):
        """Tell Twilio to hang up the call by sending a clear/stop message then closing."""
        try:
            # Send a Twilio Media Streams "clear" to stop any queued audio, then close
            await self.websocket.send_text(json.dumps({
                "event": "clear",
                "streamSid": self.stream_sid,
            }))
            await asyncio.sleep(0.5)  # give TTS audio time to finish playing
            await self.websocket.close()
        except Exception as exc:
            log.warning("[STREAM] _hangup failed: %s", exc)

    async def _execute_voice_action(self, transcript: str, action_type: str):
        """
        Run the full action pipeline and speak the result back to the guest.

        Flow (mirrors text/WhatsApp _handle_guest_inbound_message Phase 3):
          1. detect_action_intent(transcript) — get params (requested_time etc.)
          2. create_action() — persists GuestAction row with host policy check
          3. execute_action() — checks availability, applies policy, finds alternatives
          4. result_json["guest_reply"] — speak that back via TTS
        """
        try:
            from web.actions import detect_action_intent, create_action, execute_action, is_auto_approved
            from web.db import SessionLocal
            from web.models import TenantConfig, Reservation, GuestContact

            log.info("[STREAM] voice action pipeline: type=%s tenant=%s", action_type, self.tenant_id)

            # Step 1 — re-detect intent to extract params (time, guest count, etc.)
            intent = detect_action_intent(transcript)
            if not intent:
                # Voice AI already confirmed the type — use it with empty params
                intent = {"action_type": action_type, "params": {}, "confidence": 0.8}

            with SessionLocal() as db:
                cfg = db.query(TenantConfig).filter_by(tenant_id=self.tenant_id).first()

                # Step 2 — find the reservation this caller belongs to
                reservation = None
                try:
                    from web.models import VoiceCall
                    vc = db.query(VoiceCall).filter_by(id=self.call_id).first()
                    if vc and vc.guest_phone:
                        contact = db.query(GuestContact).filter_by(
                            tenant_id=self.tenant_id,
                            guest_phone=vc.guest_phone,
                        ).first()
                        if contact and contact.reservation_id:
                            reservation = db.query(Reservation).filter_by(
                                id=contact.reservation_id
                            ).first()
                except Exception as _re:
                    log.debug("[STREAM] reservation lookup: %s", _re)

                # Step 3 — create GuestAction row (policy decides approval requirement)
                guest_action = create_action(
                    db,
                    self.tenant_id,
                    intent.get("action_type", action_type),
                    intent.get("params") or {},
                    reservation,
                    None,   # no draft_id on voice calls
                    cfg,
                )

                if not guest_action:
                    return

                # Step 4 — execute if auto-approved, otherwise queue for host
                if is_auto_approved(cfg, guest_action.action_type):
                    execute_action(db, guest_action)
                    log.info("[STREAM] voice action executed: %s status=%s",
                             action_type, guest_action.status)

                    # Step 5 — speak the result back to the guest
                    reply = (guest_action.result_json or {}).get("guest_reply", "")
                    if reply:
                        await self._speak(reply)
                    elif guest_action.status == "failed":
                        await self._speak(
                            "I wasn't able to process that change directly. "
                            "I've flagged it for the host who will follow up with you shortly."
                        )
                else:
                    # Not auto-approved — host needs to confirm
                    await self._speak(
                        "I've sent that request to the host for approval. "
                        "You'll receive confirmation shortly."
                    )

        except Exception as exc:
            log.warning("[STREAM] _execute_voice_action failed: %s", exc, exc_info=True)

    async def _speak(self, text: str):
        """Synthesize to audio and stream back as μ-law chunks."""
        if not text:
            return
        try:
            from web.integrations.voice import VoiceAIService
            voice_id = self.cfg.voice_elevenlabs_voice_id if self.cfg else None
            audio_bytes, _audio_url = await VoiceAIService.synthesize_speech(text, voice_id=voice_id)
            if not audio_bytes:
                return
            mulaw = self._to_mulaw_8khz(audio_bytes)
            if not mulaw:
                return
            # Stream in 20ms chunks (160 bytes each)
            for i in range(0, len(mulaw), _TWILIO_FRAME_BYTES):
                chunk = mulaw[i:i + _TWILIO_FRAME_BYTES]
                if not chunk:
                    break
                await self.websocket.send_text(json.dumps({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(chunk).decode()},
                }))
                # Pace at real-time (20ms per frame)
                await asyncio.sleep(0.018)
        except Exception as exc:
            log.error("[STREAM] _speak failed: %s", exc, exc_info=True)

    def _to_mulaw_8khz(self, audio_bytes: bytes) -> bytes:
        """Decode WAV/MP3 to PCM16 mono 8kHz, then to μ-law."""
        try:
            import wave
            import io as _io
            # Try interpreting as WAV first
            try:
                with wave.open(_io.BytesIO(audio_bytes), "rb") as wav:
                    channels   = wav.getnchannels()
                    sample_w   = wav.getsampwidth()
                    rate       = wav.getframerate()
                    pcm        = wav.readframes(wav.getnframes())
            except wave.Error:
                # Not a WAV — try ffmpeg-style decode via pydub if available
                try:
                    from pydub import AudioSegment
                    seg = AudioSegment.from_file(_io.BytesIO(audio_bytes))
                    seg = seg.set_channels(1).set_frame_rate(_TWILIO_SAMPLE_RATE).set_sample_width(2)
                    pcm = seg.raw_data
                    channels, sample_w, rate = 1, 2, _TWILIO_SAMPLE_RATE
                except Exception as exc:
                    log.warning("[STREAM] audio decode failed (no WAV, no pydub): %s", exc)
                    return b""

            # Downmix to mono
            if channels == 2:
                pcm = audioop.tomono(pcm, sample_w, 0.5, 0.5)
            # Resample to 8 kHz
            if rate != _TWILIO_SAMPLE_RATE:
                pcm, _ = audioop.ratecv(pcm, sample_w, 1, rate, _TWILIO_SAMPLE_RATE, None)
            # Convert to 16-bit if needed
            if sample_w != 2:
                pcm = audioop.lin2lin(pcm, sample_w, 2)
            # PCM16 → μ-law
            return audioop.lin2ulaw(pcm, 2)
        except Exception as exc:
            log.error("[STREAM] _to_mulaw_8khz failed: %s", exc)
            return b""


# ── Top-level WebSocket entry point ───────────────────────────────────────

async def handle_voice_stream(websocket, call_id: str):
    """
    FastAPI WebSocket handler. Twilio Media Streams sends 4 event types:
      connected, start, media, stop
    """
    await websocket.accept()
    log.info("[STREAM] WebSocket accepted for call_id=%s", call_id)
    session: Optional[VoiceStreamSession] = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            event = msg.get("event")
            if event == "start":
                stream_sid = msg.get("streamSid") or msg.get("start", {}).get("streamSid", "")
                session = VoiceStreamSession(call_id, stream_sid, websocket)
                await session.on_start(msg.get("start") or {})
            elif event == "media" and session:
                payload = (msg.get("media") or {}).get("payload", "")
                if payload:
                    await session.on_media(payload)
            elif event == "stop":
                if session:
                    await session.on_stop()
                break
            elif event == "connected":
                log.info("[STREAM] connected event for %s", call_id)
    except Exception as exc:
        log.error("[STREAM] WebSocket error for %s: %s", call_id, exc, exc_info=True)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        log.info("[STREAM] WebSocket closed for call_id=%s", call_id)
