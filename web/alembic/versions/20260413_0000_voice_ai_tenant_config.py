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
    # Add voice AI LLM model columns
    op.add_column('tenant_configs', sa.Column('voice_llm_model', sa.String(100), nullable=False, server_default='openai/gpt-4o-mini'))
    op.add_column('tenant_configs', sa.Column('voice_llm_backup_model', sa.String(100), nullable=False, server_default='anthropic/claude-3.5-haiku'))
    op.add_column('tenant_configs', sa.Column('voice_llm_emergency_model', sa.String(100), nullable=False, server_default='meta-llama/llama-3.3-70b-instruct'))

    # Add voice AI speech recognition & synthesis columns
    op.add_column('tenant_configs', sa.Column('voice_deepgram_model', sa.String(50), nullable=False, server_default='nova-2'))
    op.add_column('tenant_configs', sa.Column('voice_llm_max_tokens', sa.Integer(), nullable=False, server_default='300'))
    op.add_column('tenant_configs', sa.Column('voice_llm_temperature', sa.Float(), nullable=False, server_default='0.7'))

    # Add ElevenLabs TTS configuration
    op.add_column('tenant_configs', sa.Column('voice_elevenlabs_stability', sa.Float(), nullable=False, server_default='0.5'))
    op.add_column('tenant_configs', sa.Column('voice_elevenlabs_similarity', sa.Float(), nullable=False, server_default='0.75'))
    op.add_column('tenant_configs', sa.Column('voice_elevenlabs_model', sa.String(50), nullable=False, server_default='eleven_turbo_v2'))


def downgrade() -> None:
    op.drop_column('tenant_configs', 'voice_elevenlabs_model')
    op.drop_column('tenant_configs', 'voice_elevenlabs_similarity')
    op.drop_column('tenant_configs', 'voice_elevenlabs_stability')
    op.drop_column('tenant_configs', 'voice_llm_temperature')
    op.drop_column('tenant_configs', 'voice_llm_max_tokens')
    op.drop_column('tenant_configs', 'voice_deepgram_model')
    op.drop_column('tenant_configs', 'voice_llm_emergency_model')
    op.drop_column('tenant_configs', 'voice_llm_backup_model')
    op.drop_column('tenant_configs', 'voice_llm_model')
