"""Resource definition tenant ownership

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The empty string represents the existing shared/unauthenticated scope.
    # A server default makes this safe for installations with existing rows.
    op.add_column("osa_resource_definitions", sa.Column("tenant_id", sa.Text(), nullable=False, server_default=""))
    op.drop_constraint(
        "uq_osa_resource_definitions_kind_name",
        "osa_resource_definitions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_osa_resource_definitions_tenant_kind_name",
        "osa_resource_definitions",
        ["tenant_id", "kind", "name"],
    )
    op.create_index("ix_osa_resource_definitions_tenant_id", "osa_resource_definitions", ["tenant_id"])
    op.alter_column("osa_resource_definitions", "tenant_id", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_osa_resource_definitions_tenant_id", table_name="osa_resource_definitions")
    op.drop_constraint(
        "uq_osa_resource_definitions_tenant_kind_name",
        "osa_resource_definitions",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_osa_resource_definitions_kind_name",
        "osa_resource_definitions",
        ["kind", "name"],
    )
    op.drop_column("osa_resource_definitions", "tenant_id")
