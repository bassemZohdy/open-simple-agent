"""Persist external A2A agent registrations.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "osa_external_agents",
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("card", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credential", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("external_id", name="pk_osa_external_agents"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_osa_external_agents_tenant_name"),
    )


def downgrade() -> None:
    op.drop_table("osa_external_agents")
