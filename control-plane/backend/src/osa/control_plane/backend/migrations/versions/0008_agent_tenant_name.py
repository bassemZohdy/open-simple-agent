"""Scope agent-name uniqueness by tenant.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("uq_osa_agents_name", "osa_agents", type_="unique")
    op.create_index(
        "uq_osa_agents_tenant_name",
        "osa_agents",
        [sa.text("coalesce(tenant_id, '')"), "name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_osa_agents_tenant_name", table_name="osa_agents")
    op.create_unique_constraint("uq_osa_agents_name", "osa_agents", ["name"])
