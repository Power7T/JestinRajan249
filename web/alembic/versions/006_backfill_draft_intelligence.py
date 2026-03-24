"""Backfill draft intelligence fields for pre-005 rows

Revision ID: 006_backfill_draft_intelligence
Revises: 005_guest_intel_ops
Create Date: 2026-03-22

Fills in:
  - guest_sentiment = 'neutral'   (no historical messages to re-classify)
  - sentiment_score = 0.0
  - guest_history_score = 0.5     (safe neutral starting point)
  - guest_message_index = 1       (already server-defaulted but ensures no NULLs)
  - auto_send_eligible = false    (already server-defaulted; old drafts don't auto-send)
  - thread_key = tenant_id || ':' || COALESCE(reply_to, id)
      Groups old drafts by the email/reply_to address they came from.

No column additions — all columns were created by migration 005.
This migration only backfills logical defaults into rows that exist.
"""
from alembic import op
import sqlalchemy as sa


revision = "006_backfill_draft_intelligence"
down_revision = "005_guest_intel_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Backfill guest_sentiment for any rows that are still NULL
    conn.execute(sa.text("""
        UPDATE drafts
        SET  guest_sentiment = 'neutral',
             sentiment_score  = 0.0
        WHERE guest_sentiment IS NULL
    """))

    # Backfill guest_history_score — use 0.5 (neutral, means "no data collected yet")
    conn.execute(sa.text("""
        UPDATE drafts
        SET  guest_history_score = 0.5
        WHERE guest_history_score IS NULL
    """))

    # Backfill thread_key for rows that don't have one yet.
    # Use tenant_id + ':' + COALESCE(reply_to, id) so old drafts for the same
    # email address/reservation_id are grouped into the same thread retroactively.
    conn.execute(sa.text("""
        UPDATE drafts
        SET  thread_key = tenant_id || ':' || COALESCE(reply_to, id)
        WHERE thread_key IS NULL
    """))

    # Ensure guest_message_index has no NULLs (server default handles new rows)
    conn.execute(sa.text("""
        UPDATE drafts
        SET  guest_message_index = 1
        WHERE guest_message_index IS NULL
    """))


def downgrade() -> None:
    # Backfill is non-destructive: downgrade clears the filled-in defaults
    # so the columns look like they did before the backfill (all NULL / default).
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE drafts
        SET  guest_sentiment      = NULL,
             sentiment_score       = NULL,
             guest_history_score   = NULL,
             thread_key            = NULL,
             guest_message_index   = 1
    """))
