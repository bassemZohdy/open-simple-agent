from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from osa.control_plane.backend.external_agents import (
    DuplicateExternalAgentError,
    ExternalAgentCatalog,
    ExternalAgentRecord,
    InMemoryExternalAgentRepository,
    PostgresExternalAgentRepository,
)


def _record(name: str, tenant_id: str | None = None) -> ExternalAgentRecord:
    return ExternalAgentRecord(
        name=name,
        url="https://partner.example.test/a2a",
        card={"name": name, "version": "1"},
        tenant_id=tenant_id,
        last_checked_at=datetime.now(UTC),
    )


async def test_in_memory_repository_is_tenant_scoped() -> None:
    repository = InMemoryExternalAgentRepository(ExternalAgentCatalog())
    first = await repository.create(_record("partner", "tenant-a"))
    await repository.create(_record("partner", "tenant-b"))

    assert await repository.get(first.external_id, tenant_id="tenant-a") is not None
    assert await repository.get(first.external_id, tenant_id="tenant-b") is None
    with pytest.raises(DuplicateExternalAgentError):
        await repository.create(_record("partner", "tenant-a"))

    first.status = "healthy"
    await repository.save(first)
    saved = await repository.get(first.external_id, tenant_id="tenant-a")
    assert saved is not None
    assert saved.status == "healthy"
    assert await repository.delete(first.external_id, tenant_id="tenant-b") is False
    assert await repository.delete(first.external_id, tenant_id="tenant-a") is True


async def test_in_memory_repository_handles_missing_records_and_closes() -> None:
    catalog = ExternalAgentCatalog()
    repository = InMemoryExternalAgentRepository(catalog)

    with pytest.raises(KeyError):
        await repository.save(_record("missing", "tenant-a"))
    assert len(catalog) == 0
    await repository.close()


def test_postgres_row_is_converted_with_credential() -> None:
    now = datetime.now(UTC)
    row = SimpleNamespace(
        external_id="external-1",
        name="partner",
        url="https://partner.example.test/a2a",
        card=None,
        status="healthy",
        detail="",
        last_checked_at=now,
        credential={"type": "api_key", "secret_ref": {"source": "env", "key": "PARTNER_KEY"}},
        tenant_id="",
        created_at=now,
        updated_at=now,
    )

    record = PostgresExternalAgentRepository._from_row(row)
    assert record.external_id == "external-1"
    assert record.card == {}
    assert record.tenant_id is None
    assert record.credential is not None


def test_postgres_repository_uses_shared_scope_for_anonymous_calls() -> None:
    assert PostgresExternalAgentRepository._scope(None) == ""
    assert PostgresExternalAgentRepository._scope("tenant-a") == "tenant-a"
