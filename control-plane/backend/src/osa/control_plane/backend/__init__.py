"""Open Simple Agent Control Plane backend."""

from osa.control_plane.backend.agent_catalog import (
    AgentCatalog,
    AgentCatalogError,
    AgentRecord,
    AgentRecordStatus,
    AgentVersion,
    DuplicateAgentError,
    DuplicateVersionError,
    InvalidTransitionError,
)
from osa.control_plane.backend.deployment import (
    Deployment,
    DeploymentProvider,
    DeploymentSpec,
    DeploymentStatus,
    LocalDeploymentProvider,
)
from osa.control_plane.backend.resource_catalogs import ResourceCatalogs
from osa.control_plane.backend.templates import (
    GENERIC_TEMPLATE,
    RESEARCH_TEMPLATE,
    SUPPORT_TEMPLATE,
    AgentTemplate,
    TemplateCatalog,
    create_default_template_catalog,
)

__all__ = [
    "AgentCatalog",
    "AgentCatalogError",
    "AgentRecord",
    "AgentRecordStatus",
    "DuplicateAgentError",
    "DuplicateVersionError",
    "InvalidTransitionError",
    "AgentTemplate",
    "AgentVersion",
    "Deployment",
    "DeploymentProvider",
    "DeploymentSpec",
    "DeploymentStatus",
    "GENERIC_TEMPLATE",
    "RESEARCH_TEMPLATE",
    "LocalDeploymentProvider",
    "ResourceCatalogs",
    "SUPPORT_TEMPLATE",
    "TemplateCatalog",
    "create_default_template_catalog",
]
