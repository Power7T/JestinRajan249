"""Add authentication columns to team_members table

Revision ID: 20260324_0320
Revises: 20260324_0315
Create Date: 2026-03-24 03:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260324_0320'
down_revision = '20260324_0315'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('team_members', sa.Column('password_hash', sa.String(length=128), nullable=True))
    op.add_column('team_members', sa.Column('invite_token', sa.String(length=64), nullable=True, index=True))
    op.add_column('team_members', sa.Column('invite_token_expires_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('team_members', 'invite_token_expires_at')
    op.drop_column('team_members', 'invite_token')
    op.drop_column('team_members', 'password_hash')
