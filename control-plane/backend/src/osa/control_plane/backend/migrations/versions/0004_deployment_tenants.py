"""Deployment tenant ownership

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("osa_deployments", sa.Column("tenant_id", sa.Text(), nullable=True))
    op.create_index("ix_osa_deployments_tenant_id", "osa_deployments", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_osa_deployments_tenant_id", table_name="osa_deployments")
    op.drop_column("osa_deployments", "tenant_id")
