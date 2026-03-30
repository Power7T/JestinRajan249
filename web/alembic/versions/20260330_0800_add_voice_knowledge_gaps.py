"""Add voice_knowledge_gaps table for tracking unanswered guest questions.

Revision ID: 20260330_0800
Revises: 20260330_0700
Create Date: 2026-03-30 08:00:00.000000
"""
from alembic import op

revision = '20260330_0800'
down_revision = '20260330_0700'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS voice_knowledge_gaps (
            id            VARCHAR(36) PRIMARY KEY,
            tenant_id     VARCHAR(36) NOT NULL REFERENCES tenants(id),
            call_id       VARCHAR(36) REFERENCES voice_calls(id),
            question      TEXT NOT NULL,
            host_answer   TEXT,
            saved_to      VARCHAR(32),
            resolved      BOOLEAN DEFAULT FALSE,
            alerted_at    TIMESTAMP WITH TIME ZONE,
            created_at    TIMESTAMP WITH TIME ZONE NOT NULL,
            resolved_at   TIMESTAMP WITH TIME ZONE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_vkg_tenant_id  ON voice_knowledge_gaps(tenant_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vkg_resolved   ON voice_knowledge_gaps(resolved)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_vkg_created_at ON voice_knowledge_gaps(created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS voice_knowledge_gaps")
