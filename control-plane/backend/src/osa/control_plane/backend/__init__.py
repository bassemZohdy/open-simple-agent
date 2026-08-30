"""Open Simple Agent Control Plane backend."""

from osa.control_plane.backend.agent_catalog import (
    AgentCatalog,
    AgentRecord,
    AgentRecordStatus,
    AgentVersion,
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
    "AgentRecord",
    "AgentRecordStatus",
    "AgentTemplate",
    "AgentVersion",
    "GENERIC_TEMPLATE",
    "RESEARCH_TEMPLATE",
    "ResourceCatalogs",
    "SUPPORT_TEMPLATE",
    "TemplateCatalog",
    "create_default_template_catalog",
]
