"""Add checkin_token_expires_at to reservations

Revision ID: 20260324_0330
Revises: 20260324_0320
Create Date: 2026-03-24 03:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "20260324_0330"
down_revision = "20260324_0320"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservations",
        sa.Column("checkin_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reservations", "checkin_token_expires_at")
