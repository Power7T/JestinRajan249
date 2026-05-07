"""Add escalated_at to voice_knowledge_gaps

Revision ID: 20260508_0300
Revises: 20260508_0200
Create Date: 2026-05-08
"""
import sqlalchemy as sa
from alembic import op

revision: str = "20260508_0300"
down_revision: str = "20260508_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_knowledge_gaps",
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("voice_knowledge_gaps", "escalated_at")
