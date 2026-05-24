"""Add missing indexes and server defaults

Revision ID: 20260508_0500
Revises: 20260508_0400
Create Date: 2026-05-08
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "20260508_0500"
down_revision: Union[str, None] = "20260508_0400"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Idempotent: production may already have these from prior partial runs / manual fixes.
    op.execute("CREATE INDEX IF NOT EXISTS ix_voice_calls_guest_email ON voice_calls (guest_email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_automation_rules_priority ON automation_rules (priority)")
    op.alter_column("tenants", "is_active", server_default="true")

def downgrade() -> None:
    op.alter_column("tenants", "is_active", server_default=None)
    op.execute("DROP INDEX IF EXISTS ix_automation_rules_priority")
    op.execute("DROP INDEX IF EXISTS ix_voice_calls_guest_email")
