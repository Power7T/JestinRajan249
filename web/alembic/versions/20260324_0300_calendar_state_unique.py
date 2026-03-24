"""Add unique constraint to calendar_states

Revision ID: c8a9b0c1d2e3
Revises: e5f6g7h8i9j0
Create Date: 2026-03-24 03:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8a9b0c1d2e3'
down_revision: Union[str, None] = 'e5f6g7h8i9j0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # First, drop any duplicates that might exist (keeping the first one created)
    op.execute("""
        DELETE FROM calendar_states
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM calendar_states
            GROUP BY tenant_id, state_key
        );
    """)
    op.create_unique_constraint(
        'uq_calendar_state_tenant_key',
        'calendar_states',
        ['tenant_id', 'state_key']
    )


def downgrade() -> None:
    op.drop_constraint('uq_calendar_state_tenant_key', 'calendar_states', type_='unique')
