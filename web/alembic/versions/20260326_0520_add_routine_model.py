"""Add routine_model to system_config for optimized routine message generation

Revision ID: 20260326_0520
Revises: 20260325_0510
Create Date: 2026-03-26 05:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260326_0520'
down_revision = '20260325_0510'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create system_config table if it doesn't exist
    # Then add routine_model column
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if system_config table exists
    if 'system_config' not in inspector.get_table_names():
        # Create the system_config table
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
    else:
        # Table exists, just add the column if it doesn't exist
        if 'routine_model' not in [col['name'] for col in inspector.get_columns('system_config')]:
            op.add_column('system_config', sa.Column(
                'routine_model',
                sa.String(length=100),
                nullable=False,
                server_default='google/gemini-2.5-flash'
            ))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Only drop the column if it exists
    if 'system_config' in inspector.get_table_names():
        if 'routine_model' in [col['name'] for col in inspector.get_columns('system_config')]:
            op.drop_column('system_config', 'routine_model')
