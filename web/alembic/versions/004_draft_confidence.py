"""Add confidence score and context_sources to drafts

Revision ID: 004_draft_confidence
Revises: 003_workflow_and_ingest_schema
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa


revision = "004_draft_confidence"
down_revision = "003_workflow_and_ingest_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.add_column(sa.Column("confidence", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("context_sources", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.drop_column("context_sources")
        batch_op.drop_column("confidence")
