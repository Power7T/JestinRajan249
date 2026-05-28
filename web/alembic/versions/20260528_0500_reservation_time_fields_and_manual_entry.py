"""Add checkin_time / checkout_time to reservations for LocalPMSAdapter writes.

Revision ID: 20260528_0500
Revises: 20260528_0400
Create Date: 2026-05-28
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0500"
down_revision: Union[str, None] = "20260528_0400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    res_cols = [c["name"] for c in inspector.get_columns("reservations")]

    if "checkin_time" not in res_cols:
        op.add_column("reservations", sa.Column("checkin_time",  sa.String(8), nullable=True))

    if "checkout_time" not in res_cols:
        op.add_column("reservations", sa.Column("checkout_time", sa.String(8), nullable=True))


def downgrade() -> None:
    op.drop_column("reservations", "checkout_time")
    op.drop_column("reservations", "checkin_time")
