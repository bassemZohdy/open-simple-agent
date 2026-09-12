"""Stable errors raised by Control Plane deployment operations."""

from __future__ import annotations


class DeploymentError(Exception):
    """A deployment operation failed."""


class DeploymentOperationError(DeploymentError):
    """A deployment operation could not obtain or retain ownership."""


class DeploymentOperationBusyError(DeploymentOperationError):
    """Another worker currently owns the deployment operation key."""


class DeploymentOperationLostError(DeploymentOperationError):
    """The worker lost its lease and must not persist a late result."""
