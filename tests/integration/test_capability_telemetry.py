"""PostgreSQL capability telemetry acceptance tests."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from osa.generic_agent import CapabilityTelemetryEvent, PostgresCapabilityTelemetrySink

pytestmark = pytest.mark.skipif(
    not os.environ.get("OSA_TEST_DATABASE_URL"),
    reason="OSA_TEST_DATABASE_URL not configured; PostgreSQL telemetry tests skipped",
)


def test_postgres_capability_sink_deduplicates_filters_and_deletes_by_tenant() -> None:
    table_name = "osa_capability_telemetry_test"
    operation_id = f"telemetry-test-{uuid4()}"
    tenant_id = f"tenant-a-{uuid4()}"
    other_tenant_id = f"tenant-b-{uuid4()}"
    sink = PostgresCapabilityTelemetrySink(
        os.environ["OSA_TEST_DATABASE_URL"],
        table_name=table_name,
        retention_days=30,
    )
    sink.create_schema()
    try:
        event = CapabilityTelemetryEvent(
            kind="model",
            name="test-model",
            outcome="success",
            error_code=None,
            duration_seconds=0.25,
            event_id=f"event-{uuid4()}",
            occurred_at=datetime.now(UTC),
            tenant_id=tenant_id,
            operation_id=operation_id,
            sequence=1,
        )
        sink.record(event)
        # A retried delivery with the same stable event id must not create a
        # second row, even when the payload differs.
        sink.record(
            CapabilityTelemetryEvent(
                kind="model",
                name="changed-on-retry",
                outcome="error",
                error_code="retry",
                duration_seconds=9.0,
                event_id=event.event_id,
                occurred_at=event.occurred_at,
                tenant_id=tenant_id,
                operation_id=operation_id,
                sequence=1,
            )
        )
        other_event = CapabilityTelemetryEvent(
            kind="tool",
            name="calculator",
            outcome="success",
            error_code=None,
            duration_seconds=0.1,
            event_id=f"event-{uuid4()}",
            occurred_at=datetime.now(UTC),
            tenant_id=other_tenant_id,
            operation_id=f"{operation_id}-other",
            sequence=1,
        )
        sink.record(other_event)

        tenant_events = [
            event for event in sink.list_events(tenant_id=tenant_id, limit=1000) if event.operation_id == operation_id
        ]
        assert len(tenant_events) == 1
        assert tenant_events[0].event_id == event.event_id
        assert tenant_events[0].name == "test-model"
        assert sink.delete_tenant(other_tenant_id) == 1
        assert not sink.list_events(tenant_id=other_tenant_id, limit=1000)
    finally:
        sink.close()
