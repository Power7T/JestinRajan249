# © 2024 Jestin Rajan. All rights reserved.
"""
Feedback Learning Loop — capture host draft edits, synthesize style notes,
inject those notes into future AI prompts so the AI improves over time.

Two entry points:
  • record_correction(...) — called from /drafts/{id}/edit when host modifies the draft
  • synthesize_learning_notes_job() — weekly cron, distills corrections into 3-bullet style notes
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from web.db import SessionLocal
from web.models import Draft, DraftCorrection, TenantConfig

log = logging.getLogger(__name__)

# Threshold for "this is a real edit" — ignore trivial whitespace tweaks
_MIN_DIFF_CHARS = 10
# Maximum corrections to feed the synthesizer (cost cap)
_MAX_CORRECTIONS_PER_SYNTHESIS = 30


def record_correction(db: Session, tenant_id: str, draft: Draft, edited_text: str) -> None:
    """
    Capture a host edit. Called from the /drafts/{id}/edit endpoint.
    Silent no-op if edit is trivial or anything fails (never blocks send).
    """
    try:
        original = (draft.draft or "").strip()
        corrected = (edited_text or "").strip()
        if not original or not corrected or original == corrected:
            return
        # Skip trivial edits (single-character corrections, tiny whitespace changes)
        if abs(len(original) - len(corrected)) < 3 and original.lower().replace(" ", "") == corrected.lower().replace(" ", ""):
            return
        if len(original) < _MIN_DIFF_CHARS and len(corrected) < _MIN_DIFF_CHARS:
            return

        db.add(DraftCorrection(
            tenant_id=tenant_id,
            draft_id=draft.id,
            original_text=original,
            corrected_text=corrected,
            guest_message=(draft.message or "")[:1000] if hasattr(draft, "message") else None,
            msg_type=getattr(draft, "msg_type", None),
            sentiment=getattr(draft, "guest_sentiment", None),
        ))
        # Increment counter on the tenant config
        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
        if cfg:
            cfg.ai_corrections_count = (cfg.ai_corrections_count or 0) + 1
        # The caller will commit
    except Exception as exc:
        log.warning("[LEARNING] record_correction failed for tenant %s: %s", tenant_id, exc)


def _synthesize_for_tenant(db: Session, tenant_id: str) -> Optional[str]:
    """
    Pull recent corrections for a tenant, send to LLM, return 3-bullet style summary.
    Returns None on any failure.
    """
    corrections = (
        db.query(DraftCorrection)
        .filter(DraftCorrection.tenant_id == tenant_id)
        .order_by(DraftCorrection.created_at.desc())
        .limit(_MAX_CORRECTIONS_PER_SYNTHESIS)
        .all()
    )
    if len(corrections) < 3:
        return None  # not enough signal to learn from

    pairs = []
    for c in corrections:
        pairs.append(f"AI wrote: {c.original_text}\nHost changed to: {c.corrected_text}")
    examples = "\n\n---\n\n".join(pairs)

    prompt = (
        "Below are recent corrections a host made to AI-generated guest reply drafts. "
        "Identify the host's style preferences and how the AI should write differently next time.\n\n"
        "Respond with ONLY a markdown bullet list of 3-5 SHORT, SPECIFIC, ACTIONABLE bullet points. "
        "Do not include explanations or preamble.\n\n"
        f"CORRECTIONS:\n{examples}"
    )

    try:
        import openai
        from web.system_config_store import load_system_config
        from web.crypto import decrypt
        sys_conf = load_system_config(db)
        if not sys_conf or not sys_conf.openrouter_api_key_enc:
            log.warning("[LEARNING] No OpenRouter key — skipping synthesis for %s", tenant_id)
            return None
        key = decrypt(sys_conf.openrouter_api_key_enc) or sys_conf.openrouter_api_key_enc
        client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
        resp = client.chat.completions.create(
            model=sys_conf.routine_model or "google/gemini-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.2,
        )
        notes = (resp.choices[0].message.content or "").strip()
        if not notes:
            return None
        # Truncate defensively
        return notes[:2000]
    except Exception as exc:
        log.warning("[LEARNING] synthesis API call failed for %s: %s", tenant_id, exc)
        return None


def synthesize_learning_notes_job() -> None:
    """
    Weekly cron job: for each tenant with recent corrections, update ai_learning_notes.
    """
    try:
        with SessionLocal() as db:
            # Tenants with corrections in the last 30 days
            cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
            tenant_ids = [
                row[0] for row in db.execute(__import__("sqlalchemy").text(
                    "SELECT DISTINCT tenant_id FROM draft_corrections "
                    "WHERE created_at > to_timestamp(:cutoff)"
                ), {"cutoff": cutoff}).fetchall()
            ]
            log.info("[LEARNING] synthesizing for %d tenants", len(tenant_ids))
            updated = 0
            for tenant_id in tenant_ids:
                try:
                    notes = _synthesize_for_tenant(db, tenant_id)
                    if notes:
                        cfg = db.query(TenantConfig).filter_by(tenant_id=tenant_id).first()
                        if cfg:
                            cfg.ai_learning_notes = notes
                            updated += 1
                except Exception as exc:
                    log.error("[LEARNING] tenant %s failed: %s", tenant_id, exc)
                    db.rollback()
            if updated:
                db.commit()
                log.info("[LEARNING] updated ai_learning_notes for %d tenants", updated)
    except Exception as exc:
        log.error("[LEARNING] job error: %s", exc, exc_info=True)


def get_recent_corrections_context(db: Session, tenant_id: str, msg_type: Optional[str] = None,
                                    limit: int = 3) -> str:
    """
    Build an in-context examples string from the 3 most recent relevant corrections.
    Used at prompt-build time to give the AI concrete "do it like this" examples.
    Returns an empty string if no corrections available.
    """
    try:
        query = db.query(DraftCorrection).filter(DraftCorrection.tenant_id == tenant_id)
        if msg_type:
            query = query.filter(DraftCorrection.msg_type == msg_type)
        corrections = query.order_by(DraftCorrection.created_at.desc()).limit(limit).all()
        if not corrections:
            return ""
        parts = ["PAST CORRECTIONS (the host edited the AI's draft to look like this — follow this style):"]
        for c in corrections:
            parts.append(f"  AI wrote: {c.original_text[:200]}")
            parts.append(f"  Host preferred: {c.corrected_text[:200]}")
            parts.append("")
        return "\n".join(parts).strip()
    except Exception as exc:
        log.warning("[LEARNING] get_recent_corrections_context failed for %s: %s", tenant_id, exc)
        return ""
