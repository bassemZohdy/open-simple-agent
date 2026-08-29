"""Agent lifecycle status."""

from __future__ import annotations

from enum import StrEnum


class AgentStatus(StrEnum):
    """Lifecycle status of a managed agent."""

    DRAFT = "draft"
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    DISABLED = "disabled"
