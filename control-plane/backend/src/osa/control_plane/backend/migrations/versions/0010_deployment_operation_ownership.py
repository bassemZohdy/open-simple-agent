"""Coordinate mutating deployment operations across Control Plane replicas.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "osa_deployment_operation_owners",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("fencing_epoch", sa.BigInteger(), nullable=False),
        sa.Column("attempt", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("last_outcome", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "resource_id",
            name="pk_osa_deployment_operation_owners",
        ),
    )
    op.create_index(
        "ix_osa_deployment_operation_owners_operation",
        "osa_deployment_operation_owners",
        ["operation_id", "owner_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_osa_deployment_operation_owners_operation", table_name="osa_deployment_operation_owners")
    op.drop_table("osa_deployment_operation_owners")
