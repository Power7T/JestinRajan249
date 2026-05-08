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
    # VoiceCall.guest_email index
    op.create_index("ix_voice_calls_guest_email", "voice_calls", ["guest_email"])
    # AutomationRule.priority index
    op.create_index("ix_automation_rules_priority", "automation_rules", ["priority"])
    # Tenant.is_active server default
    op.alter_column("tenants", "is_active", server_default="true")

def downgrade() -> None:
    op.alter_column("tenants", "is_active", server_default=None)
    op.drop_index("ix_automation_rules_priority", table_name="automation_rules")
    op.drop_index("ix_voice_calls_guest_email", table_name="voice_calls")
