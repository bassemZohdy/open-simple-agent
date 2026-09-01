"""Append-only Control Plane audit events

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "osa_audit_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("detail", sa.JSON(), nullable=False),
    )
    op.create_index("ix_osa_audit_events_tenant_occurred_at", "osa_audit_events", ["tenant_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_osa_audit_events_tenant_occurred_at", table_name="osa_audit_events")
    op.drop_table("osa_audit_events")
