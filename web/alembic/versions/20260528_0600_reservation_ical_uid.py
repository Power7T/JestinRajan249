"""Add ical_uid to reservations for iCal ↔ manual-entry cross-referencing.

When a host manually enters a reservation and later connects an iCal feed,
the sync worker can detect the same stay by (checkin, checkout, listing) and
stamp this column with the canonical iCal UID so subsequent polls find it
instantly without creating duplicates.

Revision ID: 20260528_0600
Revises: 20260528_0500
Create Date: 2026-05-28
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260528_0600"
down_revision: Union[str, None] = "20260528_0500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    res_cols = [c["name"] for c in inspector.get_columns("reservations")]

    if "ical_uid" not in res_cols:
        op.add_column(
            "reservations",
            sa.Column("ical_uid", sa.String(256), nullable=True),
        )
        op.create_index(
            "ix_reservations_ical_uid",
            "reservations",
            ["ical_uid"],
        )


def downgrade() -> None:
    op.drop_index("ix_reservations_ical_uid", table_name="reservations")
    op.drop_column("reservations", "ical_uid")
