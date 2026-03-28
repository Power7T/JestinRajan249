"""Add routine_model to system_config for optimized routine message generation

Revision ID: 20260326_0520
Revises: 20260325_0510
Create Date: 2026-03-26 05:20:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '20260326_0520'
down_revision = '20260325_0510'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create system_config table if it doesn't exist
    op.execute(
        """CREATE TABLE IF NOT EXISTS system_config (
            id VARCHAR(36) PRIMARY KEY,
            openrouter_api_key_enc VARCHAR(255),
            primary_model VARCHAR(100) NOT NULL DEFAULT 'anthropic/claude-3.5-sonnet',
            fallback_model VARCHAR(100) NOT NULL DEFAULT 'meta-llama/llama-3.1-70b-instruct',
            routine_model VARCHAR(100) NOT NULL DEFAULT 'google/gemini-2.5-flash',
            sentiment_model VARCHAR(100) NOT NULL DEFAULT 'openai/gpt-4o-mini',
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL
        )"""
    )

    # Add routine_model column to system_config if it doesn't exist (for existing tables)
    op.execute(
        """ALTER TABLE system_config ADD COLUMN IF NOT EXISTS routine_model
           VARCHAR(100) NOT NULL DEFAULT 'google/gemini-2.5-flash'"""
    )

    # Create api_usage_logs table if it doesn't exist
    op.execute(
        """CREATE TABLE IF NOT EXISTS api_usage_logs (
            id SERIAL PRIMARY KEY,
            tenant_id VARCHAR(36),
            model VARCHAR(100) NOT NULL,
            provider VARCHAR(50) NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd FLOAT NOT NULL DEFAULT 0.0,
            feature VARCHAR(50) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        )"""
    )

    # Create index if it doesn't exist (PostgreSQL will error silently if it exists)
    op.execute(
        """CREATE INDEX IF NOT EXISTS ix_api_usage_logs_tenant_id
           ON api_usage_logs(tenant_id)"""
    )


def downgrade() -> None:
    # Drop routine_model column from system_config if it exists
    op.execute(
        """ALTER TABLE system_config DROP COLUMN IF EXISTS routine_model"""
    )

    # Drop api_usage_logs table if it exists
    op.execute(
        """DROP TABLE IF EXISTS api_usage_logs"""
    )
