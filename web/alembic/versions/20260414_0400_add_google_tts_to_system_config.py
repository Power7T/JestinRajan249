"""Add Google TTS fields to system_config

Revision ID: 20260414_0400
Revises: 20260414_0300
Create Date: 2026-04-14 04:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260414_0400"
down_revision: Union[str, None] = "20260414_0300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = [
    ("google_tts_api_key_enc",        sa.String(255), None),
    ("voice_tts_provider",            sa.String(20),  "google"),
    ("voice_google_tts_voice",        sa.String(64),  "en-US-Neural2-F"),
    ("voice_google_tts_language",     sa.String(16),  "en-US"),
    ("voice_google_tts_speaking_rate", sa.Float(),    None),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "system_config" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("system_config")}

    for col_name, col_type, default in _NEW_COLUMNS:
        if col_name not in existing:
            op.add_column(
                "system_config",
                sa.Column(
                    col_name,
                    col_type,
                    nullable=True,
                    server_default=str(default) if default is not None else None,
                ),
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "system_config" not in inspector.get_table_names():
        return

    existing = {col["name"] for col in inspector.get_columns("system_config")}

    for col_name, _, _ in _NEW_COLUMNS:
        if col_name in existing:
            op.drop_column("system_config", col_name)
