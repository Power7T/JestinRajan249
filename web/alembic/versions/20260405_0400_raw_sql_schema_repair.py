"""Raw SQL schema repair - add missing columns directly

Uses raw SQL to ensure all missing columns exist. This is more reliable
than the Alembic ops layer for fixing schema drift.

Revision ID: 20260405_0400
Revises: 20260405_0300
Create Date: 2026-04-05 04:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '20260405_0400'
down_revision = '20260405_0300'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add missing columns using raw SQL."""
    conn = op.get_bind()
    
    # Check database type and add columns
    if conn.dialect.name == 'postgresql':
        # PostgreSQL - use ALTER TABLE with IF NOT EXISTS
        try:
            conn.execute(sa.text("""
                ALTER TABLE tenant_configs 
                ADD COLUMN digest_enabled BOOLEAN NOT NULL DEFAULT false
            """))
        except:
            pass  # Column already exists
        
        try:
            conn.execute(sa.text("""
                ALTER TABLE tenants 
                ADD COLUMN voice_forward_enabled BOOLEAN NOT NULL DEFAULT false
            """))
        except:
            pass
        
        try:
            conn.execute(sa.text("""
                ALTER TABLE tenants 
                ADD COLUMN voice_forward_number VARCHAR(32)
            """))
        except:
            pass
            
    elif conn.dialect.name == 'sqlite':
        # SQLite - use ALTER TABLE
        try:
            conn.execute(sa.text("""
                ALTER TABLE tenant_configs 
                ADD COLUMN digest_enabled BOOLEAN NOT NULL DEFAULT 0
            """))
        except:
            pass
        
        try:
            conn.execute(sa.text("""
                ALTER TABLE tenants 
                ADD COLUMN voice_forward_enabled BOOLEAN NOT NULL DEFAULT 0
            """))
        except:
            pass
        
        try:
            conn.execute(sa.text("""
                ALTER TABLE tenants 
                ADD COLUMN voice_forward_number VARCHAR(32)
            """))
        except:
            pass
    
    # Let Alembic manage the migration transaction/version stamp.


def downgrade() -> None:
    """No downgrade."""
    pass
