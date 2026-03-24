"""Add guest_contacts table for bot whitelisting

Revision ID: 20260325_0400
Revises: 20260324_0340
Create Date: 2026-03-25 04:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "20260325_0400"
down_revision = "20260324_0340"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guest_contacts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("reservation_id", sa.Integer(), nullable=True),
        sa.Column("guest_name", sa.String(128), nullable=False),
        sa.Column("guest_phone", sa.String(32), nullable=False),
        sa.Column("property_name", sa.String(256), nullable=True),
        sa.Column("room_identifier", sa.String(64), nullable=True),
        sa.Column("check_in", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_out", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("welcome_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("welcome_sent_to_host", sa.DateTime(timezone=True), nullable=True),
        sa.Column("welcome_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("welcome_retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reservation_id"], ["reservations.id"], ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "guest_phone", "check_in", name="uq_guest_contact"),
    )
    op.create_index("ix_guest_contacts_check_in", "guest_contacts", ["check_in"], unique=False)
    op.create_index("ix_guest_contacts_guest_phone", "guest_contacts", ["guest_phone"], unique=False)
    op.create_index("ix_guest_contacts_reservation_id", "guest_contacts", ["reservation_id"], unique=False)
    op.create_index("ix_guest_contacts_status", "guest_contacts", ["status"], unique=False)
    op.create_index("ix_guest_contacts_tenant_id", "guest_contacts", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_guest_contacts_tenant_id", table_name="guest_contacts")
    op.drop_index("ix_guest_contacts_status", table_name="guest_contacts")
    op.drop_index("ix_guest_contacts_reservation_id", table_name="guest_contacts")
    op.drop_index("ix_guest_contacts_guest_phone", table_name="guest_contacts")
    op.drop_index("ix_guest_contacts_check_in", table_name="guest_contacts")
    op.drop_table("guest_contacts")
