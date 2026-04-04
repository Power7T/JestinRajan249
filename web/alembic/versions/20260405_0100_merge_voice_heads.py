"""Merge parallel Alembic heads after voice-routing + voice-forwarding work.

Revision ID: 20260405_0100
Revises: 20260402_0100, 20260404_1000
Create Date: 2026-04-05 01:00:00.000000
"""

from typing import Sequence, Union


revision: str = "20260405_0100"
down_revision: Union[str, Sequence[str], None] = ("20260402_0100", "20260404_1000")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge revision only; schema changes already exist in parent heads.
    pass


def downgrade() -> None:
    # No-op merge reversal.
    pass
