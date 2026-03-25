"""Add SystemConfig and ApiUsageLog

Revision ID: b99205d2bc7b
Revises: 006_backfill_draft_intelligence
Create Date: 2026-03-22 10:12:39.932794+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b99205d2bc7b'
down_revision: Union[str, None] = '006_backfill_draft_intelligence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create system_config table
    op.create_table(
        'system_config',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('openrouter_api_key_enc', sa.String(length=255), nullable=True),
        sa.Column('primary_model', sa.String(length=100), nullable=False, server_default='anthropic/claude-3.5-sonnet'),
        sa.Column('fallback_model', sa.String(length=100), nullable=False, server_default='meta-llama/llama-3.1-70b-instruct'),
        sa.Column('routine_model', sa.String(length=100), nullable=False, server_default='google/gemini-2.5-flash'),
        sa.Column('sentiment_model', sa.String(length=100), nullable=False, server_default='openai/gpt-4o-mini'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Create api_usage_logs table
    op.create_table(
        'api_usage_logs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('feature', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_api_usage_logs_tenant_id', 'tenant_id')
    )


def downgrade() -> None:
    op.drop_table('api_usage_logs')
    op.drop_table('system_config')
