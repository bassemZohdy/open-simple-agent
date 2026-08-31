"""Control Plane table definitions (Core).

Single source for the repositories' queries; the Alembic migration spells the
equivalent DDL explicitly. Schema changes require a new migration.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)

METADATA = MetaData(
    schema=None,
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    },
)

agents_table = Table(
    "osa_agents",
    METADATA,
    Column("agent_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("status", Text, nullable=False),
    Column("current_version", Text, nullable=False),
    Column("runtime", Text, nullable=False, server_default="adk"),
    Column("endpoint", Text, nullable=True),
    Column("definition", JSON, nullable=True),
    Column("skills", JSON, nullable=False),
    Column("labels", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("name", name="uq_osa_agents_name"),
)

agent_versions_table = Table(
    "osa_agent_versions",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_id", Text, ForeignKey("osa_agents.agent_id", ondelete="CASCADE"), nullable=False),
    Column("version", Text, nullable=False),
    Column("definition", JSON, nullable=True),
    Column("change_summary", Text, nullable=False, server_default=""),
    Column("created_by", Text, nullable=False, server_default=""),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("agent_id", "version", name="uq_osa_agent_versions_agent_version"),
)

resource_definitions_table = Table(
    "osa_resource_definitions",
    METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("spec", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("kind", "name", name="uq_osa_resource_definitions_kind_name"),
)
