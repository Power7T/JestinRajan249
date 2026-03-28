"""Add Draft.archived_at column for conversation archiving.

Revision ID: 20260328_0610
Revises: 20260326_0530
Create Date: 2026-03-28 06:10:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260328_0610'
down_revision = '20260326_0530'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drafts', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('drafts', 'archived_at')
