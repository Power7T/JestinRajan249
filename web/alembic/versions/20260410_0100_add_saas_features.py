"""Add SaaS features: quick_replies, upsell_offers, guest satisfaction, language,
CRM notes, review request, satisfaction pulse, upsell toggle on TenantConfig.

Revision ID: 20260410_0100
Revises: 20260409_0300
Create Date: 2026-04-10 01:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260410_0100'
down_revision = '20260409_0300'
branch_labels = None
depends_on = None


def _add_col(conn, table, col, definition):
    try:
        conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {definition}"))
    except Exception:
        pass  # Column already exists


def upgrade() -> None:
    conn = op.get_bind()

    # ── TenantConfig new columns ──────────────────────────────────────────
    _add_col(conn, "tenant_configs", "digest_email_address", "VARCHAR(255)")
    _add_col(conn, "tenant_configs", "review_request_url", "VARCHAR(512)")
    _add_col(conn, "tenant_configs", "review_request_enabled", "BOOLEAN DEFAULT FALSE")
    _add_col(conn, "tenant_configs", "satisfaction_pulse_enabled", "BOOLEAN DEFAULT FALSE")
    _add_col(conn, "tenant_configs", "upsell_enabled", "BOOLEAN DEFAULT FALSE")

    # ── GuestContact new columns ──────────────────────────────────────────
    _add_col(conn, "guest_contacts", "satisfaction_sent_at", "TIMESTAMP WITH TIME ZONE")
    _add_col(conn, "guest_contacts", "satisfaction_score", "INTEGER")
    _add_col(conn, "guest_contacts", "satisfaction_scored_at", "TIMESTAMP WITH TIME ZONE")
    _add_col(conn, "guest_contacts", "language_code", "VARCHAR(8)")
    _add_col(conn, "guest_contacts", "crm_notes", "TEXT")

    # ── QuickReply table ──────────────────────────────────────────────────
    try:
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS quick_replies (
                id SERIAL PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
                label VARCHAR(128) NOT NULL,
                message_template TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_quick_replies_tenant_id ON quick_replies(tenant_id)"))
    except Exception:
        pass

    # ── UpsellOffer table ─────────────────────────────────────────────────
    try:
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS upsell_offers (
                id SERIAL PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
                offer_type VARCHAR(32) NOT NULL,
                title VARCHAR(128) NOT NULL,
                price_str VARCHAR(32) NOT NULL,
                trigger_keywords TEXT,
                message_template TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                accepted_count INTEGER DEFAULT 0,
                total_revenue FLOAT DEFAULT 0.0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_upsell_offers_tenant_id ON upsell_offers(tenant_id)"))
    except Exception:
        pass

    # Let Alembic manage the migration transaction/version stamp.


def downgrade() -> None:
    pass
