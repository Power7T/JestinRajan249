"""Add FailedDraftLog table

Revision ID: b7c8d9e0f1g2
Revises: c8a9b0c1d2e3
Create Date: 2026-03-24 03:05:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1g2'
down_revision: Union[str, None] = 'c8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'failed_draft_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('draft_id', sa.String(64), nullable=False),
        sa.Column('error_reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_failed_draft_logs_tenant_id', 'failed_draft_logs', ['tenant_id'], unique=False)
    op.create_index('ix_failed_draft_logs_draft_id', 'failed_draft_logs', ['draft_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_failed_draft_logs_draft_id', table_name='failed_draft_logs')
    op.drop_index('ix_failed_draft_logs_tenant_id', table_name='failed_draft_logs')
    op.drop_table('failed_draft_logs')
