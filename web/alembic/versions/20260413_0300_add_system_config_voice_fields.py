"""Add voice AI fields to system_config

Revision ID: 20260413_0300
Revises: 20260413_0200
Create Date: 2026-04-13 03:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260413_0300'
down_revision = '20260413_0200'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('system_config')]

    columns_to_add = {
        'voice_llm_model': sa.Column('voice_llm_model', sa.String(100), nullable=True, server_default='openai/gpt-4o-mini'),
        'voice_llm_backup_model': sa.Column('voice_llm_backup_model', sa.String(100), nullable=True, server_default='anthropic/claude-3.5-haiku'),
        'voice_llm_emergency_model': sa.Column('voice_llm_emergency_model', sa.String(100), nullable=True, server_default='meta-llama/llama-3.3-70b-instruct'),
        'voice_deepgram_model': sa.Column('voice_deepgram_model', sa.String(50), nullable=True, server_default='nova-2'),
        'voice_llm_max_tokens': sa.Column('voice_llm_max_tokens', sa.Integer(), nullable=True, server_default='300'),
        'voice_llm_temperature': sa.Column('voice_llm_temperature', sa.Float(), nullable=True, server_default='0.7'),
        'voice_elevenlabs_model': sa.Column('voice_elevenlabs_model', sa.String(50), nullable=True, server_default='eleven_turbo_v2'),
        'voice_elevenlabs_stability': sa.Column('voice_elevenlabs_stability', sa.Float(), nullable=True, server_default='0.5'),
        'voice_elevenlabs_similarity': sa.Column('voice_elevenlabs_similarity', sa.Float(), nullable=True, server_default='0.75'),
    }

    for col_name, column in columns_to_add.items():
        if col_name not in existing_columns:
            op.add_column('system_config', column)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = [col['name'] for col in inspector.get_columns('system_config')]

    for col_name in [
        'voice_elevenlabs_similarity',
        'voice_elevenlabs_stability',
        'voice_elevenlabs_model',
        'voice_llm_temperature',
        'voice_llm_max_tokens',
        'voice_deepgram_model',
        'voice_llm_emergency_model',
        'voice_llm_backup_model',
        'voice_llm_model',
    ]:
        if col_name in existing_columns:
            op.drop_column('system_config', col_name)
