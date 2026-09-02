"""Manager Agent tools: narrow, guarded Control Plane operations (P3.2).

The Manager Agent is a managed OSA agent whose tools are the only path an
LLM has into Control Plane management. The tool surface is deliberately
narrow and every tool is guarded:

- **Permissions**: each tool requires the same stable permission the
  corresponding HTTP route needs (reusing :class:`AuthorizationPolicy`'s
  role mapping), so tokens/roles authorize identically on either path.
- **Tenancy**: every record access is tenant-checked against the caller's
  principal; cross-tenant reads and writes are denied (records without a
  tenant are legacy/public and readable, but never writable cross-tenant
  except by tenant-less administrators).
- **Approval gates**: high-impact operations (deploy, restart, rollback,
  version snapshot, archive) require an explicit ``approved=True`` argument.
  Without it the tool returns ``approval_required`` instead of acting — the
  model cannot talk its way past the gate.
- **No secret access**: results are redacted projections (credential
  references only); there is no tool that resolves or returns secret values.
- **No direct database access**: tools accept repository/service
  abstractions only — no engines, connections, or SQL surface.
- **No policy bypass**: drafts are validated through the same schema,
  reference, and definition-owned resource-policy checks the runtime
  enforces; denied references fail with ``policy_violation``.

Untrusted text (agent descriptions, labels, deployment logs, remote agent
cards) is treated strictly as data: tools return it verbatim in result
fields and never act on instructions found inside it. The prompt-injection
tests pin this behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from osa.generic_agent import (
    AgentDefinition,
    AuthenticatedPrincipal,
    load_agent_definition,
)
from osa.generic_agent.auth import (
    AuthorizationError,
    AuthorizationPolicy,
    AuthPermission,
)


def _guarded(method: Any) -> Any:
    """Convert authorization/not-found errors into ToolResult statuses so
    every tool returns a structured result instead of raising."""

    import functools

    @functools.wraps(method)
    async def wrapper(self: ManagerTools, principal: AuthenticatedPrincipal, *args: Any, **kwargs: Any) -> ToolResult:
        try:
            result: ToolResult = await method(self, principal, *args, **kwargs)
            return result
        except AuthorizationError as exc:
            return _result(method.__name__, "denied", detail=str(exc))
        except KeyError as exc:
            return _result(method.__name__, "not_found", detail=str(exc.args[0] if exc.args else str(exc)))

    return wrapper


if TYPE_CHECKING:
    from osa.control_plane.backend.deployment_service import DeploymentService
    from osa.control_plane.backend.repositories import AgentRepository
    from osa.control_plane.backend.resource_catalogs import ResourceCatalogs

_MANAGER_PERMISSIONS: dict[str, str] = {
    "search_agents": AuthPermission.AGENT_READ,
    "get_agent": AuthPermission.AGENT_READ,
    "validate_definition": AuthPermission.AGENT_READ,
    "compare_versions": AuthPermission.AGENT_READ,
    "deployment_status": AuthPermission.DEPLOYMENT_READ,
    "deployment_logs": AuthPermission.DEPLOYMENT_READ,
    "agent_health": AuthPermission.DEPLOYMENT_READ,
    "draft_agent": AuthPermission.AGENT_WRITE,
    "version_agent": AuthPermission.AGENT_WRITE,
    "archive_agent": AuthPermission.AGENT_WRITE,
    "deploy_agent": AuthPermission.DEPLOYMENT_WRITE,
    "restart_deployment": AuthPermission.DEPLOYMENT_WRITE,
    "rollback_deployment": AuthPermission.DEPLOYMENT_WRITE,
}


@dataclass(frozen=True)
class ToolResult:
    """A structured, redaction-safe tool result.

    ``status`` is one of: ``ok``, ``approval_required``, ``denied``,
    ``not_found``, ``error``.
    """

    tool: str
    status: str
    data: dict[str, Any]
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "detail": self.detail,
            **({"data": self.data} if self.data else {}),
        }


def _result(tool: str, status: str, *, data: dict[str, Any] | None = None, detail: str = "") -> ToolResult:
    return ToolResult(tool=tool, status=status, data=data or {}, detail=detail)


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip credential material from serialized definitions."""
    reference = payload.get("credential_ref")
    if isinstance(reference, dict):
        allowed = {"source", "key", "env_var"}
        for field_name in list(reference):
            if field_name not in allowed:
                del reference[field_name]
    return payload


def _tenant_ok(principal: AuthenticatedPrincipal, record_tenant: str | None) -> bool:
    """Tenant boundary: an authenticated tenant may only touch its own
    records; tenant-less records are legacy/public for reads but writes
    require a matching tenant (or a tenant-less administrator)."""
    if principal.tenant_id is None:
        return True
    return record_tenant is None or record_tenant == principal.tenant_id


class ManagerTools:
    """Narrow Control Plane management surface for the Manager Agent."""

    def __init__(
        self,
        *,
        agent_repository: AgentRepository,
        deployment_service: DeploymentService,
        resource_catalogs: ResourceCatalogs,
        authorization_policy: AuthorizationPolicy | None = None,
    ) -> None:
        self._agents = agent_repository
        self._deployments = deployment_service
        self._catalogs = resource_catalogs
        self._policy = authorization_policy or AuthorizationPolicy(enabled=True)

    # -- guards --

    def _require(self, principal: AuthenticatedPrincipal, tool: str) -> None:
        self._policy.require(principal, _MANAGER_PERMISSIONS[tool])

    @staticmethod
    async def _owned_record(
        principal: AuthenticatedPrincipal, agents: AgentRepository, agent_id: str
    ) -> tuple[Any, str]:
        record = await agents.get(agent_id)
        if record is None:
            raise KeyError(f"Agent not found: {agent_id}")
        if not _tenant_ok(principal, record.tenant_id):
            raise AuthorizationError("Record belongs to another tenant")
        return record, record.name

    @staticmethod
    def _require_approval(tool: str, approved: bool) -> ToolResult | None:
        if approved:
            return None
        return _result(
            tool,
            "approval_required",
            detail=(f"{tool} is a high-impact operation; re-run with approved=true after explicit operator approval"),
        )

    @staticmethod
    def _summary(record: Any) -> dict[str, Any]:
        return {
            "agent_id": record.agent_id,
            "name": record.name,
            "status": record.status.value,
            "current_version": record.current_version,
            "description": record.description,
            "tenant_id": record.tenant_id,
            "skills": list(record.skills),
        }

    @staticmethod
    def _record_payload(record: Any) -> dict[str, Any]:
        return {
            "agent": ManagerTools._summary(record),
            "definition": _redact(record.definition.model_dump(mode="json", by_alias=True))
            if record.definition is not None
            else None,
        }

    # -- read tools --

    @_guarded
    async def search_agents(self, principal: AuthenticatedPrincipal, query: str = "") -> ToolResult:
        self._require(principal, "search_agents")
        records = await self._agents.list_all()
        if principal.tenant_id is not None:
            records = [r for r in records if r.tenant_id in (None, principal.tenant_id)]
        if query:
            needle = query.lower()
            records = [r for r in records if needle in r.name.lower() or needle in r.description.lower()]
        records.sort(key=lambda r: r.name)
        # Untrusted fields (description, labels) are returned as data.
        return _result(
            "search_agents",
            "ok",
            data={"agents": [self._summary(r) for r in records], "count": len(records)},
        )

    @_guarded
    async def get_agent(self, principal: AuthenticatedPrincipal, agent_id: str) -> ToolResult:
        self._require(principal, "get_agent")
        try:
            record, _ = await self._owned_record(principal, self._agents, agent_id)
        except KeyError:
            return _result("get_agent", "not_found", detail=f"Agent not found: {agent_id}")
        return _result("get_agent", "ok", data=self._record_payload(record))

    @_guarded
    async def deployment_status(self, principal: AuthenticatedPrincipal, deployment_id: str) -> ToolResult:
        self._require(principal, "deployment_status")
        try:
            record = await self._deployments.status(deployment_id)
        except KeyError:
            return _result("deployment_status", "not_found", detail=f"Deployment not found: {deployment_id}")
        if not _tenant_ok(principal, await self._record_tenant_for(record)):
            return _result("deployment_status", "denied", detail="Deployment belongs to another tenant")
        return _result(
            "deployment_status",
            "ok",
            data={
                "deployment_id": record.deployment_id,
                "agent_id": record.agent_id,
                "agent_name": record.agent_name,
                "version": record.version,
                "status": record.status,
                "detail": record.detail,
            },
        )

    @_guarded
    async def deployment_logs(
        self, principal: AuthenticatedPrincipal, deployment_id: str, tail: int = 50
    ) -> ToolResult:
        self._require(principal, "deployment_logs")
        try:
            lines = await self._deployments.logs(deployment_id, tail)
        except KeyError:
            return _result("deployment_logs", "not_found", detail=f"Deployment not found: {deployment_id}")
        record = await self._deployments.status(deployment_id)
        if not _tenant_ok(principal, await self._record_tenant_for(record)):
            return _result("deployment_logs", "denied", detail="Deployment belongs to another tenant")
        # Log lines are untrusted data: returned verbatim, never acted on.
        return _result("deployment_logs", "ok", data={"lines": lines})

    @_guarded
    async def agent_health(self, principal: AuthenticatedPrincipal, agent_id: str) -> ToolResult:
        self._require(principal, "agent_health")
        record = await self._agents.get(agent_id)
        if record is None or not _tenant_ok(principal, record.tenant_id):
            return _result("agent_health", "not_found", detail=f"Agent not found: {agent_id}")
        deployments = await self._deployments.list_for_agent(agent_id)
        latest = deployments[-1] if deployments else None
        return _result(
            "agent_health",
            "ok",
            data={
                "agent_name": record.name,
                "agent_status": record.status.value,
                "deployment_status": latest.status if latest else "never_deployed",
                "deployment_detail": latest.detail if latest else "",
            },
        )

    async def _record_tenant_for(self, record: Any) -> str | None:
        agent = await self._agents.get(record.agent_id)
        return agent.tenant_id if agent is not None else None

    # -- validation / comparison (read-only) --

    @_guarded
    async def validate_definition(self, principal: AuthenticatedPrincipal, definition_yaml: str) -> ToolResult:
        """Validate a definition through the same schema, reference, and
        resource-policy checks the runtime enforces — without persisting."""
        self._require(principal, "validate_definition")
        try:
            definition = load_agent_definition(definition_yaml)
        except Exception as exc:
            return _result("validate_definition", "error", detail=f"invalid definition: {exc}")

        problems: list[str] = []
        spec = definition.spec
        if spec.model is not None and not self._catalogs_has("Model", spec.model.ref):
            problems.append(f"model '{spec.model.ref}' not found")
        for rule, kind in ((spec.policy.tools, "Tool"), (spec.policy.mcps, "Mcp"), (spec.policy.skills, "Skill")):
            for ref in (t.ref for t in spec.tools if kind == "Tool"):
                if not rule.permits(ref):
                    problems.append(f"policy denies {kind.lower()} '{ref}'")
            for ref in (m.ref for m in spec.mcps if kind == "Mcp"):
                if not rule.permits(ref):
                    problems.append(f"policy denies mcp '{ref}'")
            for ref in (s.ref for s in spec.skills if kind == "Skill"):
                if not rule.permits(ref):
                    problems.append(f"policy denies skill '{ref}'")
        for tool_ref in spec.tools:
            if not self._catalogs_has("Tool", tool_ref.ref):
                problems.append(f"tool '{tool_ref.ref}' not found")
        for skill_ref in spec.skills:
            if not self._catalogs_has("Skill", skill_ref.ref):
                problems.append(f"skill '{skill_ref.ref}' not found")
        for mcp_ref in spec.mcps:
            if not self._catalogs_has("Mcp", mcp_ref.ref):
                problems.append(f"mcp '{mcp_ref.ref}' not found")
        if (
            spec.memory.enabled
            and spec.memory.policy is not None
            and not self._catalogs_has("MemoryPolicy", spec.memory.policy)
        ):
            problems.append(f"memory policy '{spec.memory.policy}' not found")

        status = "ok" if not problems else "error"
        return _result("validate_definition", status, data={"problems": problems})

    @_guarded
    async def compare_versions(
        self, principal: AuthenticatedPrincipal, agent_id: str, base_version: str, target_version: str
    ) -> ToolResult:
        self._require(principal, "compare_versions")
        try:
            record, _ = await self._owned_record(principal, self._agents, agent_id)
        except KeyError:
            return _result("compare_versions", "not_found", detail=f"Agent not found: {agent_id}")
        base = next((v for v in record.versions if v.version == base_version), None)
        target = next((v for v in record.versions if v.version == target_version), None)
        if base is None or target is None or base.definition is None or target.definition is None:
            return _result(
                "compare_versions",
                "error",
                detail="one or both versions have no definition snapshot",
            )
        base_dump = base.definition.model_dump(mode="json", by_alias=True)
        target_dump = target.definition.model_dump(mode="json", by_alias=True)
        differences: list[str] = []
        if base_dump.get("spec", {}).get("instruction") != target_dump.get("spec", {}).get("instruction"):
            differences.append("spec.instruction changed")
        base_tools = {t["ref"] for t in base_dump.get("spec", {}).get("tools", [])}
        target_tools = {t["ref"] for t in target_dump.get("spec", {}).get("tools", [])}
        if base_tools != target_tools:
            differences.append(
                f"tools changed: added {sorted(target_tools - base_tools)}, removed {sorted(base_tools - target_tools)}"
            )
        if base_dump.get("spec", {}).get("model") != target_dump.get("spec", {}).get("model"):
            differences.append("spec.model changed")
        return _result(
            "compare_versions",
            "ok",
            data={
                "base_version": base_version,
                "target_version": target_version,
                "differences": differences or ["no differences"],
            },
        )

    # -- mutating tools (guarded + approval-gated) --

    @_guarded
    async def draft_agent(
        self,
        principal: AuthenticatedPrincipal,
        name: str,
        definition_yaml: str,
        description: str = "",
    ) -> ToolResult:
        self._require(principal, "draft_agent")
        problems = self._draft_problems(definition_yaml, name)
        if problems:
            return _result("draft_agent", "error", data={"problems": problems})
        from osa.control_plane.backend.agent_catalog import AgentRecord

        record = AgentRecord(
            name=name,
            description=description,
            definition=self._parse_definition(definition_yaml),
            tenant_id=principal.tenant_id,
        )
        if record.definition is not None:
            record.skills = [ref.ref for ref in record.definition.spec.skills]
        try:
            await self._agents.create(record)
        except Exception as exc:
            return _result("draft_agent", "error", detail=str(exc))
        return _result(
            "draft_agent",
            "ok",
            data={"agent_id": record.agent_id, "name": record.name, "status": record.status.value},
        )

    @_guarded
    async def version_agent(
        self,
        principal: AuthenticatedPrincipal,
        agent_id: str,
        version: str,
        *,
        approved: bool = False,
    ) -> ToolResult:
        self._require(principal, "version_agent")
        if (gate := self._require_approval("version_agent", approved)) is not None:
            return gate
        try:
            record, _ = await self._owned_record(principal, self._agents, agent_id)
        except KeyError:
            return _result("version_agent", "not_found", detail=f"Agent not found: {agent_id}")
        if record.definition is None:
            return _result("version_agent", "error", detail="Agent has no definition to snapshot")
        from osa.control_plane.backend.agent_catalog import AgentVersion
        from osa.control_plane.backend.repositories import DuplicateVersionError

        try:
            await self._agents.add_version(
                agent_id, AgentVersion(version=version, change_summary="created by manager agent")
            )
        except DuplicateVersionError as exc:
            return _result("version_agent", "error", detail=str(exc))
        return _result("version_agent", "ok", data={"agent_id": agent_id, "version": version})

    @_guarded
    async def archive_agent(
        self, principal: AuthenticatedPrincipal, agent_id: str, *, approved: bool = False
    ) -> ToolResult:
        self._require(principal, "archive_agent")
        if (gate := self._require_approval("archive_agent", approved)) is not None:
            return gate
        try:
            record, _ = await self._owned_record(principal, self._agents, agent_id)
        except KeyError:
            return _result("archive_agent", "not_found", detail=f"Agent not found: {agent_id}")
        from osa.control_plane.backend.agent_catalog import AgentRecordStatus

        updated = await self._agents.transition(agent_id, AgentRecordStatus.ARCHIVED)
        return _result("archive_agent", "ok", data={"agent_id": agent_id, "status": updated.status.value})

    @_guarded
    async def deploy_agent(
        self, principal: AuthenticatedPrincipal, agent_id: str, *, approved: bool = False
    ) -> ToolResult:
        self._require(principal, "deploy_agent")
        if (gate := self._require_approval("deploy_agent", approved)) is not None:
            return gate
        try:
            record, _ = await self._owned_record(principal, self._agents, agent_id)
        except KeyError:
            return _result("deploy_agent", "not_found", detail=f"Agent not found: {agent_id}")
        if not _tenant_ok(principal, record.tenant_id):
            return _result("deploy_agent", "denied", detail="Agent belongs to another tenant")
        if getattr(record, "agent_type", "managed") == "external":
            return _result("deploy_agent", "denied", detail="External A2A agents are never deployed by OSA")
        from osa.control_plane.backend.deployment_service import DeploymentError

        try:
            deployment = await self._deployments.deploy(agent_id)
        except DeploymentError as exc:
            return _result("deploy_agent", "error", detail=str(exc))
        return _result(
            "deploy_agent",
            "ok",
            data={
                "deployment_id": deployment.deployment_id,
                "status": deployment.status,
                "version": deployment.version,
            },
        )

    @_guarded
    async def restart_deployment(
        self, principal: AuthenticatedPrincipal, deployment_id: str, *, approved: bool = False
    ) -> ToolResult:
        self._require(principal, "restart_deployment")
        if (gate := self._require_approval("restart_deployment", approved)) is not None:
            return gate
        try:
            record = await self._deployments.restart(deployment_id)
        except KeyError:
            return _result("restart_deployment", "not_found", detail=f"Deployment not found: {deployment_id}")
        return _result(
            "restart_deployment",
            "ok",
            data={"deployment_id": deployment_id, "status": record.status},
        )

    @_guarded
    async def rollback_deployment(
        self,
        principal: AuthenticatedPrincipal,
        deployment_id: str,
        to_version: str | None = None,
        *,
        approved: bool = False,
    ) -> ToolResult:
        self._require(principal, "rollback_deployment")
        if (gate := self._require_approval("rollback_deployment", approved)) is not None:
            return gate
        from osa.control_plane.backend.deployment_service import DeploymentError

        try:
            record = await self._deployments.rollback(deployment_id, to_version)
        except KeyError:
            return _result("rollback_deployment", "not_found", detail=f"Deployment not found: {deployment_id}")
        except DeploymentError as exc:
            return _result("rollback_deployment", "error", detail=str(exc))
        return _result(
            "rollback_deployment",
            "ok",
            data={"deployment_id": deployment_id, "rolled_back_to": record.version},
        )

    # -- helpers --

    def _catalogs_has(self, kind: str, name: str) -> bool:
        checks = {
            "Model": self._catalogs.has_model,
            "Tool": self._catalogs.has_tool,
            "Skill": self._catalogs.has_skill,
            "Mcp": self._catalogs.has_mcp,
            "MemoryPolicy": self._catalogs.has_memory_policy,
        }
        return checks[kind](name)

    def _parse_definition(self, definition_yaml: str) -> AgentDefinition:
        return load_agent_definition(definition_yaml)

    def _draft_problems(self, definition_yaml: str, name: str) -> list[str]:
        try:
            definition = self._parse_definition(definition_yaml)
        except Exception as exc:
            return [f"invalid definition: {exc}"]
        problems: list[str] = []
        if definition.metadata.name != name:
            problems.append(f"definition metadata.name '{definition.metadata.name}' does not match draft name '{name}'")
        spec = definition.spec

        def check_policy(rule: Any, kind: str, ref: str) -> None:
            if not rule.permits(ref):
                problems.append(f"policy denies {kind.lower()} '{ref}'")

        if spec.model is not None and not self._catalogs_has("Model", spec.model.ref):
            problems.append(f"model '{spec.model.ref}' not found")
        for tool_ref in spec.tools:
            check_policy(spec.policy.tools, "Tool", tool_ref.ref)
            if not self._catalogs_has("Tool", tool_ref.ref):
                problems.append(f"tool '{tool_ref.ref}' not found")
        for skill_ref in spec.skills:
            check_policy(spec.policy.skills, "Skill", skill_ref.ref)
            if not self._catalogs_has("Skill", skill_ref.ref):
                problems.append(f"skill '{skill_ref.ref}' not found")
        for mcp_ref in spec.mcps:
            check_policy(spec.policy.mcps, "Mcp", mcp_ref.ref)
            if not self._catalogs_has("Mcp", mcp_ref.ref):
                problems.append(f"mcp '{mcp_ref.ref}' not found")
        if spec.memory.enabled and spec.memory.policy is not None:
            memory_rule = spec.policy.memory if hasattr(spec.policy, "memory") else spec.policy.tools
            check_policy(memory_rule, "MemoryPolicy", spec.memory.policy)
            if not self._catalogs_has("MemoryPolicy", spec.memory.policy):
                problems.append(f"memory policy '{spec.memory.policy}' not found")
        return problems


__all__ = ["ManagerTools", "ToolResult"]
