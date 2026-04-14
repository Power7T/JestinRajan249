"""Repair admin Voice AI schema and normalize system_config.

Revision ID: 20260414_0300
Revises: 20260414_0200
Create Date: 2026-04-14 03:00:00.000000+00:00
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260414_0300"
down_revision: Union[str, None] = "20260414_0200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SYSTEM_CONFIG_TABLE = "system_config"
_TENANT_CONFIG_TABLE = "tenant_configs"
_PRIORITY_FIELDS = (
    "openrouter_api_key_enc",
    "deepgram_api_key_enc",
    "elevenlabs_api_key_enc",
    "primary_model",
    "routine_model",
    "voice_llm_model",
    "voice_deepgram_model",
    "voice_elevenlabs_model",
    "voice_elevenlabs_voice_id",
)
_SYSTEM_CONFIG_COLUMNS: dict[str, sa.Column[Any]] = {
    "openrouter_api_key_enc": sa.Column("openrouter_api_key_enc", sa.String(length=255), nullable=True),
    "google_maps_api_key_enc": sa.Column("google_maps_api_key_enc", sa.String(length=255), nullable=True),
    "deepgram_api_key_enc": sa.Column("deepgram_api_key_enc", sa.String(length=255), nullable=True),
    "elevenlabs_api_key_enc": sa.Column("elevenlabs_api_key_enc", sa.String(length=255), nullable=True),
    "cloudflare_account_id": sa.Column("cloudflare_account_id", sa.String(length=255), nullable=True),
    "cloudflare_r2_access_key_enc": sa.Column("cloudflare_r2_access_key_enc", sa.String(length=255), nullable=True),
    "cloudflare_r2_secret_key_enc": sa.Column("cloudflare_r2_secret_key_enc", sa.String(length=255), nullable=True),
    "cloudflare_r2_bucket": sa.Column("cloudflare_r2_bucket", sa.String(length=255), nullable=True),
    "primary_model": sa.Column("primary_model", sa.String(length=100), nullable=True, server_default="mistralai/mistral-large-2512"),
    "routine_model": sa.Column("routine_model", sa.String(length=100), nullable=True, server_default="google/gemini-2.5-flash"),
    "primary_backup_model": sa.Column("primary_backup_model", sa.String(length=100), nullable=True, server_default="anthropic/claude-3.5-sonnet"),
    "routine_backup_model": sa.Column("routine_backup_model", sa.String(length=100), nullable=True, server_default="anthropic/claude-3.5-haiku"),
    "fallback_model": sa.Column("fallback_model", sa.String(length=100), nullable=True, server_default="meta-llama/llama-3.3-70b-instruct"),
    "sentiment_model": sa.Column("sentiment_model", sa.String(length=100), nullable=True, server_default="openai/gpt-4o-mini"),
    "voice_llm_model": sa.Column("voice_llm_model", sa.String(length=100), nullable=True, server_default="openai/gpt-4o-mini"),
    "voice_llm_backup_model": sa.Column("voice_llm_backup_model", sa.String(length=100), nullable=True, server_default="anthropic/claude-3.5-haiku"),
    "voice_llm_emergency_model": sa.Column("voice_llm_emergency_model", sa.String(length=100), nullable=True, server_default="meta-llama/llama-3.3-70b-instruct"),
    "voice_deepgram_model": sa.Column("voice_deepgram_model", sa.String(length=50), nullable=True, server_default="nova-2"),
    "voice_llm_max_tokens": sa.Column("voice_llm_max_tokens", sa.Integer(), nullable=True, server_default="300"),
    "voice_llm_temperature": sa.Column("voice_llm_temperature", sa.Float(), nullable=True, server_default="0.7"),
    "voice_elevenlabs_model": sa.Column("voice_elevenlabs_model", sa.String(length=50), nullable=True, server_default="eleven_turbo_v2"),
    "voice_elevenlabs_stability": sa.Column("voice_elevenlabs_stability", sa.Float(), nullable=True, server_default="0.5"),
    "voice_elevenlabs_similarity": sa.Column("voice_elevenlabs_similarity", sa.Float(), nullable=True, server_default="0.75"),
    "voice_elevenlabs_voice_id": sa.Column("voice_elevenlabs_voice_id", sa.String(length=64), nullable=True, server_default="EXAVITQu4vr4xnSDxMaL"),
    "updated_at": sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
}
_TENANT_VOICE_COLUMNS: dict[str, sa.Column[Any]] = {
    "voice_send_channel": sa.Column("voice_send_channel", sa.String(length=16), nullable=True, server_default="disabled"),
    "voice_post_call_summary": sa.Column("voice_post_call_summary", sa.Boolean(), nullable=True, server_default=sa.text("false")),
    "voice_scheduled_calls_enabled": sa.Column("voice_scheduled_calls_enabled", sa.Boolean(), nullable=True, server_default=sa.text("false")),
    "voice_twilio_account_sid": sa.Column("voice_twilio_account_sid", sa.String(length=64), nullable=True),
    "voice_twilio_auth_token_enc": sa.Column("voice_twilio_auth_token_enc", sa.Text(), nullable=True),
    "voice_twilio_from_number": sa.Column("voice_twilio_from_number", sa.String(length=32), nullable=True),
    "voice_elevenlabs_voice_id": sa.Column("voice_elevenlabs_voice_id", sa.String(length=64), nullable=True, server_default="EXAVITQu4vr4xnSDxMaL"),
    "voice_llm_model": sa.Column("voice_llm_model", sa.String(length=100), nullable=True, server_default="openai/gpt-4o-mini"),
    "voice_llm_backup_model": sa.Column("voice_llm_backup_model", sa.String(length=100), nullable=True, server_default="anthropic/claude-3.5-haiku"),
    "voice_llm_emergency_model": sa.Column("voice_llm_emergency_model", sa.String(length=100), nullable=True, server_default="meta-llama/llama-3.3-70b-instruct"),
    "voice_deepgram_model": sa.Column("voice_deepgram_model", sa.String(length=50), nullable=True, server_default="nova-2"),
    "voice_llm_max_tokens": sa.Column("voice_llm_max_tokens", sa.Integer(), nullable=True, server_default="300"),
    "voice_llm_temperature": sa.Column("voice_llm_temperature", sa.Float(), nullable=True, server_default="0.7"),
    "voice_elevenlabs_stability": sa.Column("voice_elevenlabs_stability", sa.Float(), nullable=True, server_default="0.5"),
    "voice_elevenlabs_similarity": sa.Column("voice_elevenlabs_similarity", sa.Float(), nullable=True, server_default="0.75"),
    "voice_elevenlabs_model": sa.Column("voice_elevenlabs_model", sa.String(length=50), nullable=True, server_default="eleven_turbo_v2"),
}


def _is_populated(value: object | None) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _as_datetime(value: object | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def _config_rank(row: Mapping[str, Any]) -> tuple[int, datetime, str]:
    priority = 0
    populated = 0
    for column_name in row.keys():
        if column_name == "id":
            continue
        value = row.get(column_name)
        if _is_populated(value):
            populated += 1
            if column_name in _PRIORITY_FIELDS:
                priority += 1
    return (priority * 100 + populated, _as_datetime(row.get("updated_at")), str(row.get("id") or ""))


def _merge_duplicate_system_config_rows(conn) -> None:
    inspector = sa.inspect(conn)
    if _SYSTEM_CONFIG_TABLE not in inspector.get_table_names():
        return

    metadata = sa.MetaData()
    table = sa.Table(_SYSTEM_CONFIG_TABLE, metadata, autoload_with=conn)
    if "id" not in table.c:
        return

    rows = conn.execute(sa.select(table)).mappings().all()
    if len(rows) <= 1:
        return

    ranked_rows = sorted(rows, key=_config_rank, reverse=True)
    canonical = ranked_rows[0]
    merged_values: dict[str, Any] = {}

    for column in table.columns:
        if column.name == "id":
            continue
        if column.name == "updated_at":
            continue

        chosen = canonical.get(column.name)
        if _is_populated(chosen):
            merged_values[column.name] = chosen
            continue

        for row in ranked_rows[1:]:
            candidate = row.get(column.name)
            if _is_populated(candidate):
                merged_values[column.name] = candidate
                break

    if "updated_at" in table.c:
        merged_values["updated_at"] = datetime.now(timezone.utc)

    conn.execute(
        sa.update(table)
        .where(table.c.id == canonical["id"])
        .values(**merged_values)
    )
    conn.execute(sa.delete(table).where(table.c.id != canonical["id"]))


def _ensure_columns(conn, table_name: str, columns: Mapping[str, sa.Column[Any]]) -> None:
    inspector = sa.inspect(conn)
    if table_name not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
    for column_name, column in columns.items():
        if column_name not in existing_columns:
            op.add_column(table_name, column)


def upgrade() -> None:
    conn = op.get_bind()
    _ensure_columns(conn, _SYSTEM_CONFIG_TABLE, _SYSTEM_CONFIG_COLUMNS)
    _ensure_columns(conn, _TENANT_CONFIG_TABLE, _TENANT_VOICE_COLUMNS)
    _merge_duplicate_system_config_rows(conn)


def downgrade() -> None:
    """No-op downgrade for additive schema repair."""
    pass
