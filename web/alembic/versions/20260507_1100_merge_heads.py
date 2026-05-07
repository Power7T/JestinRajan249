"""Merge parallel migration heads

Revision ID: 20260507_1100
Revises: 20260428_1000, 20260507_1000
Create Date: 2026-05-07
"""
from typing import Union
from alembic import op

revision: str = "20260507_1100"
down_revision: Union[tuple, None] = ("20260428_1000", "20260507_1000")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
