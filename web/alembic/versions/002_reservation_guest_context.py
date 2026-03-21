"""Add reservation guest context fields.

Revision ID: 002
Revises: 001
Create Date: 2026-03-21 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reservations", sa.Column("guest_phone", sa.String(length=32), nullable=True))
    op.add_column("reservations", sa.Column("unit_identifier", sa.String(length=64), nullable=True))
    op.create_index("ix_reservations_guest_phone", "reservations", ["guest_phone"], unique=False)
    op.create_index("ix_reservations_unit_identifier", "reservations", ["unit_identifier"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reservations_unit_identifier", table_name="reservations")
    op.drop_index("ix_reservations_guest_phone", table_name="reservations")
    op.drop_column("reservations", "unit_identifier")
    op.drop_column("reservations", "guest_phone")
