"""Add SaaS reliability features: rate limiting, cost tracking, idempotency, feature flags, call consent.

Revision ID: 20260330_1230
Revises: 20260330_1220
Create Date: 2026-03-30 12:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '20260330_1230'
down_revision = '20260330_1220'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add recording consent fields to voice_calls
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS recording_consent_given "
        "BOOLEAN NULL"
    )
    op.execute(
        "ALTER TABLE voice_calls ADD COLUMN IF NOT EXISTS recording_consent_at "
        "TIMESTAMP WITH TIME ZONE NULL"
    )

    # Create idempotency_keys table
    op.execute("""
        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key_id VARCHAR(128) PRIMARY KEY,
            tenant_id VARCHAR(36) NOT NULL,
            idempotency_key VARCHAR(128) NOT NULL UNIQUE,
            operation VARCHAR(128) NOT NULL,
            result_status VARCHAR(32) DEFAULT 'pending',
            result_data JSON NULL,
            result_error VARCHAR(512) NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_idempotency_keys_tenant_id ON idempotency_keys(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_idempotency_keys_expires_at ON idempotency_keys(expires_at)"
    )

    # Create tenant_rate_limits table
    op.execute("""
        CREATE TABLE IF NOT EXISTS tenant_rate_limits (
            tenant_id VARCHAR(36) PRIMARY KEY,
            voice_calls_per_hour INTEGER DEFAULT 100,
            external_api_calls_per_hour INTEGER DEFAULT 500,
            max_daily_cost_usd INTEGER DEFAULT 50,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    """)

    # Create rate_limit_counters table
    op.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit_counters (
            counter_id VARCHAR(128) PRIMARY KEY,
            tenant_id VARCHAR(36) NOT NULL,
            metric VARCHAR(32) NOT NULL,
            count INTEGER DEFAULT 0,
            window_start TIMESTAMP WITH TIME ZONE NOT NULL,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rate_limit_counters_tenant_id ON rate_limit_counters(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rate_limit_counters_expires_at ON rate_limit_counters(expires_at)"
    )

    # Create api_usage_logs table
    op.execute("""
        CREATE TABLE IF NOT EXISTS api_usage_logs (
            id VARCHAR(36) PRIMARY KEY,
            tenant_id VARCHAR(36) NOT NULL,
            call_id VARCHAR(36) NULL,
            service VARCHAR(32) NOT NULL,
            operation VARCHAR(64) NOT NULL,
            input_tokens INTEGER NULL,
            output_tokens INTEGER NULL,
            duration_seconds FLOAT NULL,
            characters INTEGER NULL,
            cost_usd FLOAT DEFAULT 0.0,
            status VARCHAR(16) DEFAULT 'success',
            error_message VARCHAR(512) NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
            FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_usage_logs_tenant_id ON api_usage_logs(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_usage_logs_call_id ON api_usage_logs(call_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_api_usage_logs_created_at ON api_usage_logs(created_at)"
    )

    # Create feature_flags table
    op.execute("""
        CREATE TABLE IF NOT EXISTS feature_flags (
            flag_name VARCHAR(128) PRIMARY KEY,
            enabled BOOLEAN DEFAULT FALSE,
            rollout_percentage INTEGER DEFAULT 0,
            description VARCHAR(512) NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create feature_flag_overrides table
    op.execute("""
        CREATE TABLE IF NOT EXISTS feature_flag_overrides (
            id VARCHAR(128) PRIMARY KEY,
            flag_name VARCHAR(128) NOT NULL,
            tenant_id VARCHAR(36) NOT NULL,
            enabled BOOLEAN NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_feature_flag_overrides_tenant_id ON feature_flag_overrides(tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_feature_flag_overrides_flag_name ON feature_flag_overrides(flag_name)"
    )


def downgrade() -> None:
    # Drop tables and columns in reverse order
    op.execute("DROP TABLE IF EXISTS feature_flag_overrides")
    op.execute("DROP TABLE IF EXISTS feature_flags")
    op.execute("DROP TABLE IF EXISTS api_usage_logs")
    op.execute("DROP TABLE IF EXISTS rate_limit_counters")
    op.execute("DROP TABLE IF EXISTS tenant_rate_limits")
    op.execute("DROP TABLE IF EXISTS idempotency_keys")

    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS recording_consent_at")
    op.execute("ALTER TABLE voice_calls DROP COLUMN IF EXISTS recording_consent_given")
