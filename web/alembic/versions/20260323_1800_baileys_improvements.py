"""Baileys improvements - add status tracking, idempotency, token expiration

Revision ID: e5f6g7h8i9j0
Revises: d2e3f4a5b6c7
Create Date: 2026-03-23 18:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6g7h8i9j0'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns to baileys_outbound
    op.add_column('baileys_outbound', sa.Column('status', sa.String(32), nullable=False, server_default='pending'))
    op.add_column('baileys_outbound', sa.Column('error_reason', sa.Text(), nullable=True))
    op.add_column('baileys_outbound', sa.Column('idempotency_key', sa.String(64), nullable=True))
    op.add_column('baileys_outbound', sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('baileys_outbound', sa.Column('last_retry_at', sa.DateTime(timezone=True), nullable=True))
    
    op.create_index('ix_baileys_outbound_status', 'baileys_outbound', ['status'], unique=False)
    op.create_index('ix_baileys_outbound_idempotency_key', 'baileys_outbound', ['idempotency_key'], unique=True)
    op.create_index('ix_baileys_outbound_to_phone', 'baileys_outbound', ['to_phone'], unique=False)

    # Create baileys_callbacks table
    op.create_table(
        'baileys_callbacks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('draft_id', sa.String(64), nullable=True),
        sa.Column('action', sa.String(32), nullable=False),
        sa.Column('idempotency_key', sa.String(128), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.ForeignKeyConstraint(['draft_id'], ['drafts.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_baileys_callbacks_tenant_id', 'baileys_callbacks', ['tenant_id'], unique=False)
    op.create_index('ix_baileys_callbacks_idempotency_key', 'baileys_callbacks', ['idempotency_key'], unique=True)

    # Add columns to tenant_configs (bot_api_token_hint already exists in initial schema)
    op.add_column('tenant_configs', sa.Column('bot_api_token_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tenant_configs', sa.Column('bot_last_heartbeat', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tenant_configs', sa.Column('baileys_max_batch_size', sa.Integer(), nullable=False, server_default='50'))
    op.add_column('tenant_configs', sa.Column('baileys_max_per_minute', sa.Integer(), nullable=False, server_default='60'))
    
    op.create_index('ix_tenant_configs_bot_api_token_hash', 'tenant_configs', ['bot_api_token_hash'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tenant_configs_bot_api_token_hash', table_name='tenant_configs')
    op.drop_column('tenant_configs', 'baileys_max_per_minute')
    op.drop_column('tenant_configs', 'baileys_max_batch_size')
    op.drop_column('tenant_configs', 'bot_last_heartbeat')
    op.drop_column('tenant_configs', 'bot_api_token_expires_at')
    # bot_api_token_hint is in initial schema, don't drop it

    op.drop_index('ix_baileys_callbacks_idempotency_key', table_name='baileys_callbacks')
    op.drop_index('ix_baileys_callbacks_tenant_id', table_name='baileys_callbacks')
    op.drop_table('baileys_callbacks')

    op.drop_index('ix_baileys_outbound_to_phone', table_name='baileys_outbound')
    op.drop_index('ix_baileys_outbound_idempotency_key', table_name='baileys_outbound')
    op.drop_index('ix_baileys_outbound_status', table_name='baileys_outbound')
    op.drop_column('baileys_outbound', 'last_retry_at')
    op.drop_column('baileys_outbound', 'retry_count')
    op.drop_column('baileys_outbound', 'idempotency_key')
    op.drop_column('baileys_outbound', 'error_reason')
    op.drop_column('baileys_outbound', 'status')
