"""Add voice_google_tts_voice to tenant_configs

Revision ID: 20260415_0500
Revises: 20260414_0400
Create Date: 2026-04-15 05:00:00.000000+00:00
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260415_0500"
down_revision: Union[str, None] = "20260414_0400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "tenant_configs" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("tenant_configs")}
    if "voice_google_tts_voice" not in existing:
        op.add_column("tenant_configs", sa.Column("voice_google_tts_voice", sa.String(64), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "tenant_configs" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("tenant_configs")}
    if "voice_google_tts_voice" in existing:
        op.drop_column("tenant_configs", "voice_google_tts_voice")
