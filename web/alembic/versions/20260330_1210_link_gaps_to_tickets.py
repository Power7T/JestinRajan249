"""Link VoiceKnowledgeGap to IssueTicket for ticket integration.

Revision ID: 20260330_1210
Revises: 20260330_1200
Create Date: 2026-03-30 12:10:00.000000
"""
from alembic import op


revision = '20260330_1210'
down_revision = '20260330_1200'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add issue_ticket_id FK to voice_knowledge_gaps
    op.execute(
        "ALTER TABLE voice_knowledge_gaps ADD COLUMN IF NOT EXISTS issue_ticket_id "
        "INTEGER REFERENCES issue_tickets(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE voice_knowledge_gaps DROP COLUMN IF EXISTS issue_ticket_id")
