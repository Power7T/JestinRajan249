"""Add voice_elevenlabs_voice_id to system_config

Revision ID: 20260414_0200
Revises: 20260413_0000
Create Date: 2026-04-14 02:00:00.000000+00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260414_0200"
down_revision: Union[str, None] = "20260413_0000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("system_config")}

    if "voice_elevenlabs_voice_id" not in existing_columns:
        op.add_column(
            "system_config",
            sa.Column(
                "voice_elevenlabs_voice_id",
                sa.String(64),
                nullable=True,
                server_default="EXAVITQu4vr4xnSDxMaL",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("system_config")}

    if "voice_elevenlabs_voice_id" in existing_columns:
        op.drop_column("system_config", "voice_elevenlabs_voice_id")
