"""Add guest identity and reply-sent tracking to voice_knowledge_gaps.

Revision ID: 20260330_0900
Revises: 20260330_0800
Create Date: 2026-03-30 09:00:00.000000
"""
from alembic import op

revision = '20260330_0900'
down_revision = '20260330_0800'
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = [
        "ALTER TABLE voice_knowledge_gaps ADD COLUMN IF NOT EXISTS guest_phone  VARCHAR(32)",
        "ALTER TABLE voice_knowledge_gaps ADD COLUMN IF NOT EXISTS guest_name   VARCHAR(128)",
        "ALTER TABLE voice_knowledge_gaps ADD COLUMN IF NOT EXISTS guest_room   VARCHAR(64)",
        "ALTER TABLE voice_knowledge_gaps ADD COLUMN IF NOT EXISTS reply_sent   BOOLEAN DEFAULT FALSE",
        "ALTER TABLE voice_knowledge_gaps ADD COLUMN IF NOT EXISTS reply_sent_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE voice_knowledge_gaps ADD COLUMN IF NOT EXISTS reply_channel VARCHAR(16)",
    ]
    for col in cols:
        op.execute(col)


def downgrade() -> None:
    for col in ["reply_channel", "reply_sent_at", "reply_sent",
                "guest_room", "guest_name", "guest_phone"]:
        op.execute(f"ALTER TABLE voice_knowledge_gaps DROP COLUMN IF EXISTS {col}")
