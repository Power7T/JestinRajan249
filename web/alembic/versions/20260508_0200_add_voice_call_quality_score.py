"""Add quality_score to voice_calls

Revision ID: 20260508_0200
Revises: 20260508_0100
Create Date: 2026-05-08
"""
import sqlalchemy as sa
from alembic import op

revision: str = "20260508_0200"
down_revision: str = "20260508_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "voice_calls",
        sa.Column("quality_score", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("voice_calls", "quality_score")
