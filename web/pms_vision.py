"""
Vision PMS Adapter — Playwright + DOM-first + Gemini Flash vision fallback.

Works with ANY web-based PMS that has no API.

Strategy (cheapest-first):
  1. DOM extraction → text LLM to pick element → Playwright clicks (~$0.0001/step, ~500ms)
  2. Screenshot + Gemini Flash vision fallback (~$0.0003/step, ~1.2s)

Per-task cost: ~$0.002  |  Per-task time: 5–8s (async background)
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, date, timezone
from typing import Optional, Any

from web.pms_base import PMSAdapter, PMSMessage, PMSReservation

log = logging.getLogger(__name__)

_MAX_STEPS = 15
_STEP_DELAY_MS = 700
_SCREENSHOT_QUALITY = 60  # JPEG quality — keeps image small for faster API calls


class VisionPMSAdapter(PMSAdapter):
    """
    Browser-controlled PMS adapter for systems with no REST API.

    api_key format: "username|||password"
    account_id:     JSON string with optional config:
                    {"pms_name": "Opera", "capabilities": {...}, "session_cookies": [...]}
    base_url:       The login URL of the PMS web interface

    Vision can do most actions, just slower than API.
    """
    SUPPORTS_UPDATE_RESERVATION = True
    SUPPORTS_ADD_NOTE           = True
    SUPPORTS_BLOCK_DATES        = True
    SUPPORTS_AVAILABILITY       = True

    def __init__(self, api_key: str, account_id: str = "", base_url: str = ""):
        parts = api_key.split("|||", 1)
        self._username = parts[0].strip()
        self._password = parts[1].strip() if len(parts) > 1 else ""
        self._base_url = base_url.rstrip("/")
        try:
            self._meta: dict = json.loads(account_id) if account_id else {}
        except Exception:
            self._meta = {}
        self._capabilities: dict = self._meta.get("capabilities", {})
        self._pms_name: str = self._meta.get("pms_name", "Unknown PMS")

    # ── internal helpers ──────────────────────────────────────────────────

    async def _get_openrouter_key(self) -> Optional[str]:
        """Fetch OpenRouter key from DB SystemConfig."""
        try:
            from web.db import SessionLocal
            from web.models import SystemConfig
            from web.crypto import decrypt
            with SessionLocal() as db:
                sys_conf = db.query(SystemConfig).first()
                if sys_conf and sys_conf.openrouter_api_key_enc:
                    return decrypt(sys_conf.openrouter_api_key_enc)
        except Exception as exc:
            log.warning("[VISION PMS] Could not fetch OpenRouter key: %s", exc)
        return None

    async def _dom_extract(self, page) -> list[dict]:
        """Extract all interactive elements from the DOM as structured data."""
        try:
            elements = await page.evaluate("""
                () => {
                    const els = [...document.querySelectorAll(
                        'a, button, input:not([type=hidden]), select, textarea, [role="button"], [onclick], [role="tab"], [role="menuitem"]'
                    )];
                    return els.map((el, i) => {
                        const rect = el.getBoundingClientRect();
                        const text = (el.innerText || el.value || el.placeholder ||
                                     el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 80);
                        return {
                            i,
                            tag: el.tagName.toLowerCase(),
                            text,
                            type: el.type || '',
                            href: el.href || '',
                            visible: rect.width > 0 && rect.height > 0 && rect.top >= 0,
                        };
                    }).filter(e => e.text && e.visible).slice(0, 60);
                }
            """)
            return elements or []
        except Exception:
            return []

    async def _llm_decide_dom(self, task: str, elements: list[dict],
                               history: list[str], step: int) -> dict:
        """Use a text LLM to pick the right DOM element for the current task step."""
        import openai
        key = await self._get_openrouter_key()
        if not key:
            return {"action": "vision_fallback"}

        elements_text = "\n".join(
            f"  [{e['i']}] <{e['tag']} type={e['type']}> \"{e['text']}\" href={e['href'][:40] if e['href'] else ''}"
            for e in elements
        )
        history_text = "\n".join(f"  Step {i+1}: {h}" for i, h in enumerate(history[-5:]))

        prompt = f"""You are controlling a property management system browser via automation.

TASK: {task}
STEP: {step + 1} of max {_MAX_STEPS}
RECENT ACTIONS:
{history_text or '  (none yet)'}

INTERACTIVE ELEMENTS ON SCREEN:
{elements_text or '  (none found — page may be loading or requires vision)'}

Respond with ONLY a JSON object (no markdown):
{{
  "action": "click" | "type" | "wait" | "done" | "error" | "vision_fallback",
  "element_index": 5,          // index from the list above (for click/type)
  "text_to_type": "...",       // for type action
  "reason": "brief explanation"
}}

Rules:
- "done" when the task is complete
- "vision_fallback" if the element list is empty or confusing (needs screenshot)
- "wait" if page seems to be loading (no useful elements)
- "error" only if the task is impossible given what you see"""

        try:
            client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=key,
            )
            resp = client.chat.completions.create(
                model="openai/gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0,
            )
            raw = resp.choices[0].message.content.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except Exception as exc:
            log.warning("[VISION PMS] DOM LLM failed: %s — falling back to vision", exc)
            return {"action": "vision_fallback"}

    async def _llm_decide_vision(self, task: str, screenshot_bytes: bytes,
                                  history: list[str], step: int) -> dict:
        """Use Gemini Flash vision to decide next action from screenshot."""
        import openai
        key = await self._get_openrouter_key()
        if not key:
            return {"action": "error", "reason": "No OpenRouter key configured"}

        img_b64 = base64.b64encode(screenshot_bytes).decode()
        history_text = "\n".join(f"  Step {i+1}: {h}" for i, h in enumerate(history[-5:]))

        prompt = f"""You are controlling a property management system (PMS) web interface via browser automation.

TASK: {task}
STEP: {step + 1} of max {_MAX_STEPS}
RECENT ACTIONS:
{history_text or '  (none yet)'}

Look at the screenshot. Respond with ONLY a JSON object (no markdown):
{{
  "screen_description": "what you see in 1 sentence",
  "action": "click" | "type" | "navigate" | "wait" | "done" | "error",
  "coordinates": [x, y],         // for click (pixel coordinates on the screenshot)
  "text_to_type": "...",          // for type action
  "url": "...",                   // for navigate
  "reason": "brief explanation"
}}

Rules:
- Coordinates must be within the screenshot dimensions
- "done" when the task is complete
- "wait" if page is loading
- "error" only if task is truly impossible"""

        try:
            client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=key,
            )
            resp = client.chat.completions.create(
                model="google/gemini-2.5-flash",
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                        },
                        {"type": "text", "text": prompt}
                    ]
                }],
                max_tokens=300,
                temperature=0,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except Exception as exc:
            log.error("[VISION PMS] Vision LLM failed: %s", exc)
            return {"action": "error", "reason": str(exc)}

    async def _execute_action(self, page, decision: dict, elements: list[dict]) -> str:
        """Execute the LLM's decided action. Returns a log string."""
        action = decision.get("action", "error")

        if action == "click":
            idx = decision.get("element_index")
            coords = decision.get("coordinates")
            if idx is not None and elements:
                try:
                    # Click by evaluating element at index
                    await page.evaluate(
                        f"""() => {{
                            const els = [...document.querySelectorAll(
                                'a, button, input:not([type=hidden]), select, textarea, [role="button"], [onclick], [role="tab"], [role="menuitem"]'
                            )].filter(el => {{
                                const r = el.getBoundingClientRect();
                                return (el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim()
                                    && r.width > 0 && r.height > 0 && r.top >= 0;
                            }});
                            if (els[{idx}]) els[{idx}].click();
                        }}"""
                    )
                    return f"clicked element [{idx}]: {elements[idx]['text'] if idx < len(elements) else '?'}"
                except Exception as exc:
                    log.warning("[VISION PMS] Click by index failed: %s", exc)
            if coords and len(coords) == 2:
                await page.mouse.click(coords[0], coords[1])
                return f"clicked at ({coords[0]}, {coords[1]})"

        elif action == "type":
            text = decision.get("text_to_type", "")
            idx = decision.get("element_index")
            if idx is not None and elements:
                try:
                    await page.evaluate(
                        f"""() => {{
                            const els = [...document.querySelectorAll(
                                'input:not([type=hidden]), textarea, select'
                            )].filter(el => {{
                                const r = el.getBoundingClientRect();
                                return r.width > 0 && r.height > 0;
                            }});
                            if (els[{idx}]) {{ els[{idx}].focus(); els[{idx}].value = ''; }}
                        }}"""
                    )
                except Exception:
                    pass
            await page.keyboard.type(text, delay=30)
            return f"typed: {text[:40]}"

        elif action == "navigate":
            url = decision.get("url", "")
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                return f"navigated to {url}"

        elif action == "wait":
            await page.wait_for_timeout(1500)
            return "waited for page"

        return action

    async def _agent_loop(self, page, task: str, max_steps: int = _MAX_STEPS) -> bool:
        """
        Main agent loop: DOM extraction → LLM decide → execute → repeat.
        Falls back to vision if DOM gives no elements.
        """
        history: list[str] = []

        for step in range(max_steps):
            await page.wait_for_timeout(_STEP_DELAY_MS)

            # Primary: DOM-based decision
            elements = await self._dom_extract(page)
            decision = await self._llm_decide_dom(task, elements, history, step)

            # Fallback: vision-based decision
            if decision.get("action") == "vision_fallback" or not elements:
                screenshot = await page.screenshot(
                    type="jpeg", quality=_SCREENSHOT_QUALITY, full_page=False
                )
                decision = await self._llm_decide_vision(task, screenshot, history, step)
                elements = []  # don't need elements for vision-based actions

            action = decision.get("action", "error")
            log.info("[VISION PMS] Step %d: %s — %s", step+1, action, decision.get("reason", ""))

            if action == "done":
                log.info("[VISION PMS] Task complete in %d steps", step+1)
                return True
            if action == "error":
                log.error("[VISION PMS] Task failed: %s", decision.get("reason", "unknown"))
                return False

            action_log = await self._execute_action(page, decision, elements)
            history.append(action_log)

        log.warning("[VISION PMS] Max steps (%d) reached without completing task", max_steps)
        return False

    async def _login(self, page) -> bool:
        """Navigate to base_url and log in with stored credentials."""
        try:
            await page.goto(self._base_url, wait_until="domcontentloaded", timeout=20000)
            # Try direct field filling first (most login pages)
            try:
                await page.fill('input[type="email"], input[name*="user"], input[name*="email"], input[id*="user"], input[id*="email"]',
                                self._username, timeout=3000)
                await page.fill('input[type="password"]', self._password, timeout=3000)
                await page.click('button[type="submit"], input[type="submit"], button:has-text("Login"), button:has-text("Sign in")',
                                timeout=3000)
                await page.wait_for_timeout(2000)
                return True
            except Exception:
                pass
            # Fall back to vision agent for login
            login_task = f'Log in with username "{self._username}" and password (already filled, just submit the form or fill credentials)'
            return await self._agent_loop(page, login_task, max_steps=6)
        except Exception as exc:
            log.error("[VISION PMS] Login failed: %s", exc)
            return False

    async def _detect_capabilities(self, page) -> dict:
        """Take a screenshot of the dashboard and identify what the PMS can do."""
        import openai
        key = await self._get_openrouter_key()
        if not key:
            return {}

        screenshot = await page.screenshot(type="jpeg", quality=_SCREENSHOT_QUALITY)
        img_b64 = base64.b64encode(screenshot).decode()

        prompt = """This is a screenshot of a property management system (PMS) dashboard.

Identify the system and its capabilities. Respond with ONLY JSON:
{
  "pms_name": "detected PMS name or 'Unknown PMS'",
  "has_inbox": true/false,
  "has_reservations": true/false,
  "has_calendar": true/false,
  "has_pricing": true/false,
  "nav_items": ["list", "of", "visible", "navigation", "items"],
  "confidence": 0.0-1.0
}"""

        try:
            client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
            resp = client.chat.completions.create(
                model="google/gemini-2.5-flash",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }],
                max_tokens=300,
                temperature=0,
            )
            raw = resp.choices[0].message.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())
        except Exception as exc:
            log.warning("[VISION PMS] Capability detection failed: %s", exc)
            return {}

    # ── PMSAdapter interface ──────────────────────────────────────────────

    def test_connection(self) -> bool:
        """Verify credentials by attempting a login via Playwright."""
        return asyncio.run(self._async_test_connection())

    async def _async_test_connection(self) -> bool:
        if not self._base_url:
            log.warning("[VISION PMS] No base_url configured")
            return False
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("[VISION PMS] playwright not installed — run: pip install playwright && playwright install chromium")
            return False

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                logged_in = await self._login(page)
                if logged_in:
                    caps = await self._detect_capabilities(page)
                    if caps:
                        self._capabilities = caps
                        self._pms_name = caps.get("pms_name", "Unknown PMS")
                        log.info("[VISION PMS] Detected: %s | capabilities: %s", self._pms_name, caps)
                return logged_in
            finally:
                await browser.close()

    def get_new_messages(self, since: datetime) -> list[PMSMessage]:
        """Use vision agent to navigate to inbox and extract recent guest messages."""
        return asyncio.run(self._async_get_messages(since))

    async def _async_get_messages(self, since: datetime) -> list[PMSMessage]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("[VISION PMS] playwright not installed")
            return []

        messages: list[PMSMessage] = []
        since_str = since.strftime("%Y-%m-%d %H:%M")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                if not await self._login(page):
                    return []

                # Navigate to inbox
                task = f"Navigate to the messages or inbox section where I can see guest messages received after {since_str}"
                if not await self._agent_loop(page, task, max_steps=8):
                    log.warning("[VISION PMS] Could not navigate to inbox")
                    return []

                # Extract visible messages via vision
                import openai
                key = await self._get_openrouter_key()
                if not key:
                    return []

                screenshot = await page.screenshot(type="jpeg", quality=_SCREENSHOT_QUALITY)
                img_b64 = base64.b64encode(screenshot).decode()

                extract_prompt = f"""This is an inbox/messages screen from a property management system.
Extract all visible guest messages received after {since_str}.

Respond with ONLY JSON:
{{
  "messages": [
    {{
      "message_id": "unique id or index",
      "reservation_id": "reservation or booking id if visible",
      "guest_name": "Guest Name",
      "text": "message content",
      "received_at": "YYYY-MM-DD HH:MM",
      "channel": "airbnb|booking|direct|unknown"
    }}
  ]
}}

If no messages are visible or all are older than {since_str}, return {{"messages": []}}"""

                client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
                resp = client.chat.completions.create(
                    model="google/gemini-2.5-flash",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                            {"type": "text", "text": extract_prompt}
                        ]
                    }],
                    max_tokens=1000,
                    temperature=0,
                )
                raw = resp.choices[0].message.content.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = json.loads(raw.strip())

                for m in data.get("messages", []):
                    try:
                        ts_str = m.get("received_at", "")
                        try:
                            msg_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        except Exception:
                            msg_time = datetime.now(timezone.utc)

                        if msg_time.tzinfo is None:
                            msg_time = msg_time.replace(tzinfo=timezone.utc)
                        if msg_time <= since:
                            continue

                        messages.append(PMSMessage(
                            message_id=str(m.get("message_id", f"vision_{len(messages)}")),
                            reservation_id=str(m.get("reservation_id", "")),
                            guest_name=str(m.get("guest_name", "Guest")),
                            text=str(m.get("text", "")),
                            received_at=msg_time,
                            channel=str(m.get("channel", "direct")),
                        ))
                    except Exception as exc:
                        log.warning("[VISION PMS] Failed to parse message: %s", exc)
            finally:
                await browser.close()

        return messages

    def send_message(self, reservation_id: str, text: str) -> bool:
        """Use vision agent to find the reservation conversation and send a reply."""
        return asyncio.run(self._async_send_message(reservation_id, text))

    async def _async_send_message(self, reservation_id: str, text: str) -> bool:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("[VISION PMS] playwright not installed")
            return False

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                if not await self._login(page):
                    return False

                safe_text = text.replace('"', '\\"').replace('\n', ' ')
                task = (
                    f'Find the conversation for reservation/booking "{reservation_id}" '
                    f'and send this message: "{safe_text[:200]}"'
                )
                success = await self._agent_loop(page, task, max_steps=_MAX_STEPS)
                if success:
                    log.info("[VISION PMS] Sent message for reservation %s", reservation_id)
                else:
                    log.error("[VISION PMS] Failed to send message for reservation %s", reservation_id)
                return success
            finally:
                await browser.close()

    def get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        """Use vision agent to navigate to reservations and extract data."""
        return asyncio.run(self._async_get_reservations(from_date, to_date))

    async def _async_get_reservations(self, from_date: date, to_date: date) -> list[PMSReservation]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return []

        reservations: list[PMSReservation] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                if not await self._login(page):
                    return []

                task = f"Navigate to reservations or bookings list showing arrivals from {from_date} to {to_date}"
                if not await self._agent_loop(page, task, max_steps=8):
                    return []

                import openai
                key = await self._get_openrouter_key()
                if not key:
                    return []

                screenshot = await page.screenshot(type="jpeg", quality=_SCREENSHOT_QUALITY)
                img_b64 = base64.b64encode(screenshot).decode()

                extract_prompt = f"""This is a reservations/bookings screen from a property management system.
Extract all visible reservations with check-in from {from_date} to {to_date}.

Respond with ONLY JSON:
{{
  "reservations": [
    {{
      "reservation_id": "...",
      "confirmation_code": "...",
      "guest_name": "...",
      "listing_name": "...",
      "checkin": "YYYY-MM-DD",
      "checkout": "YYYY-MM-DD",
      "guests_count": 2,
      "status": "confirmed|cancelled|pending"
    }}
  ]
}}"""

                client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
                resp = client.chat.completions.create(
                    model="google/gemini-2.5-flash",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                            {"type": "text", "text": extract_prompt}
                        ]
                    }],
                    max_tokens=1000,
                    temperature=0,
                )
                raw = resp.choices[0].message.content.strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = json.loads(raw.strip())

                for r in data.get("reservations", []):
                    try:
                        reservations.append(PMSReservation(
                            reservation_id=str(r.get("reservation_id", "")),
                            confirmation_code=str(r.get("confirmation_code", r.get("reservation_id", ""))),
                            guest_name=str(r.get("guest_name", "Guest")),
                            listing_name=str(r.get("listing_name", "")),
                            checkin=_parse_date(r.get("checkin")),
                            checkout=_parse_date(r.get("checkout")),
                            guests_count=int(r.get("guests_count") or 1),
                            status=str(r.get("status", "confirmed")).lower(),
                        ))
                    except Exception as exc:
                        log.warning("[VISION PMS] Failed to parse reservation: %s", exc)
            finally:
                await browser.close()

        return reservations

    # ── Phase 3: Agentic actions via vision agent ────────────────────────

    def update_reservation(self, reservation_id: str, changes: dict) -> bool:
        """Use vision agent to find a reservation and apply changes via UI clicks."""
        return asyncio.run(self._async_update_reservation(reservation_id, changes))

    async def _async_update_reservation(self, reservation_id: str, changes: dict) -> bool:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.error("[VISION PMS] playwright not installed")
            return False
        if not reservation_id or not changes:
            return False

        change_desc = ", ".join(f'{k}={v}' for k, v in changes.items())
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                if not await self._login(page):
                    return False
                task = (
                    f'Find reservation/booking "{reservation_id}" and update its fields: '
                    f'{change_desc}. Save the changes when done.'
                )
                return await self._agent_loop(page, task, max_steps=_MAX_STEPS)
            finally:
                await browser.close()

    def add_note(self, reservation_id: str, note: str) -> bool:
        return asyncio.run(self._async_add_note(reservation_id, note))

    async def _async_add_note(self, reservation_id: str, note: str) -> bool:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return False
        if not reservation_id or not note:
            return False
        safe_note = note.replace('"', '\\"').replace('\n', ' ')[:400]
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                if not await self._login(page):
                    return False
                task = (
                    f'Find reservation/booking "{reservation_id}" and add this internal staff '
                    f'note to it: "{safe_note}". Save the note.'
                )
                return await self._agent_loop(page, task, max_steps=_MAX_STEPS)
            finally:
                await browser.close()

    def block_dates(self, listing_id: str, from_date: date, to_date: date, reason: str = "") -> bool:
        return asyncio.run(self._async_block_dates(listing_id, from_date, to_date, reason))

    async def _async_block_dates(self, listing_id: str, from_date: date, to_date: date, reason: str = "") -> bool:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return False
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                if not await self._login(page):
                    return False
                safe_reason = (reason or "Blocked via HostAI").replace('"', "'")[:120]
                task = (
                    f'Open the calendar/availability view for listing "{listing_id}", '
                    f'and block the dates from {from_date.isoformat()} to {to_date.isoformat()}. '
                    f'Use the reason: "{safe_reason}". Save the change.'
                )
                return await self._agent_loop(page, task, max_steps=_MAX_STEPS)
            finally:
                await browser.close()

    def get_availability(self, listing_id: str, from_date: date, to_date: date) -> bool:
        return asyncio.run(self._async_get_availability(listing_id, from_date, to_date))

    async def _async_get_availability(self, listing_id: str, from_date: date, to_date: date) -> bool:
        """Take a screenshot of the calendar and ask the vision LLM if dates are free."""
        try:
            from playwright.async_api import async_playwright
            import openai
        except ImportError:
            return False
        key = await self._get_openrouter_key()
        if not key:
            return False
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            try:
                if not await self._login(page):
                    return False
                task = f'Open the calendar/availability view for listing "{listing_id}" showing dates around {from_date} to {to_date}'
                if not await self._agent_loop(page, task, max_steps=6):
                    return False
                screenshot = await page.screenshot(type="jpeg", quality=_SCREENSHOT_QUALITY)
                img_b64 = base64.b64encode(screenshot).decode()
                prompt = (
                    f"Look at this calendar/availability screen. "
                    f"Are ALL of the dates from {from_date.isoformat()} to {to_date.isoformat()} "
                    f"shown as available (not blocked, not booked)?\n"
                    f'Respond with ONLY JSON: {{"available": true|false}}'
                )
                client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
                resp = client.chat.completions.create(
                    model="google/gemini-2.5-flash",
                    messages=[{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                        {"type": "text", "text": prompt},
                    ]}],
                    max_tokens=50,
                    temperature=0,
                )
                raw = (resp.choices[0].message.content or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                data = json.loads(raw.strip())
                return bool(data.get("available"))
            except Exception as exc:
                log.error("[VISION PMS] get_availability failed: %s", exc)
                return False
            finally:
                await browser.close()


def _parse_date(val) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val)[:10]).date()
    except Exception:
        return None
