"""initial control plane schema

Revision ID: 0001
Revises: None
Create Date: 2026-08-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "osa_agents",
        sa.Column("agent_id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_version", sa.Text(), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False, server_default="adk"),
        sa.Column("endpoint", sa.Text(), nullable=True),
        sa.Column("definition", sa.JSON(), nullable=True),
        sa.Column("skills", sa.JSON(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_osa_agents_name"),
    )
    op.create_table(
        "osa_agent_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id",
            sa.Text(),
            sa.ForeignKey("osa_agents.agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "version", name="uq_osa_agent_versions_agent_version"),
    )
    op.create_table(
        "osa_resource_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "name", name="uq_osa_resource_definitions_kind_name"),
    )


def downgrade() -> None:
    op.drop_table("osa_resource_definitions")
    op.drop_table("osa_agent_versions")
    op.drop_table("osa_agents")
