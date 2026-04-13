"""Add voice AI configuration columns to tenant_configs

Revision ID: 20260413_0000
Revises: 20260412_0000
Create Date: 2026-04-13 00:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260413_0000'
down_revision: Union[str, None] = '20260410_0100'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col['name'] for col in inspector.get_columns('tenant_configs')}

    columns_to_add = {
        'voice_llm_model': sa.Column('voice_llm_model', sa.String(100), nullable=False, server_default='openai/gpt-4o-mini'),
        'voice_llm_backup_model': sa.Column('voice_llm_backup_model', sa.String(100), nullable=False, server_default='anthropic/claude-3.5-haiku'),
        'voice_llm_emergency_model': sa.Column('voice_llm_emergency_model', sa.String(100), nullable=False, server_default='meta-llama/llama-3.3-70b-instruct'),
        'voice_deepgram_model': sa.Column('voice_deepgram_model', sa.String(50), nullable=False, server_default='nova-2'),
        'voice_llm_max_tokens': sa.Column('voice_llm_max_tokens', sa.Integer(), nullable=False, server_default='300'),
        'voice_llm_temperature': sa.Column('voice_llm_temperature', sa.Float(), nullable=False, server_default='0.7'),
        'voice_elevenlabs_stability': sa.Column('voice_elevenlabs_stability', sa.Float(), nullable=False, server_default='0.5'),
        'voice_elevenlabs_similarity': sa.Column('voice_elevenlabs_similarity', sa.Float(), nullable=False, server_default='0.75'),
        'voice_elevenlabs_model': sa.Column('voice_elevenlabs_model', sa.String(50), nullable=False, server_default='eleven_turbo_v2'),
    }

    for column_name, column in columns_to_add.items():
        if column_name not in existing_columns:
            op.add_column('tenant_configs', column)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col['name'] for col in inspector.get_columns('tenant_configs')}

    for column_name in [
        'voice_elevenlabs_model',
        'voice_elevenlabs_similarity',
        'voice_elevenlabs_stability',
        'voice_llm_temperature',
        'voice_llm_max_tokens',
        'voice_deepgram_model',
        'voice_llm_emergency_model',
        'voice_llm_backup_model',
        'voice_llm_model',
    ]:
        if column_name in existing_columns:
            op.drop_column('tenant_configs', column_name)
