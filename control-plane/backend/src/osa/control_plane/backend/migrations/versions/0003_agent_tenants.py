"""Agent tenant ownership

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("osa_agents", sa.Column("tenant_id", sa.Text(), nullable=True))
    op.create_index("ix_osa_agents_tenant_id", "osa_agents", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_osa_agents_tenant_id", table_name="osa_agents")
    op.drop_column("osa_agents", "tenant_id")
