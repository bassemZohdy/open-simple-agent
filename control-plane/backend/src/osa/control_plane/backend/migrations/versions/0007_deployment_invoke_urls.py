"""Deployment invoke URLs

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("osa_deployments", sa.Column("invoke_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("osa_deployments", "invoke_url")
