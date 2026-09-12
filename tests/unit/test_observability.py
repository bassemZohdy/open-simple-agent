"""Tests for redaction-safe structured observability."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import pytest

from osa.generic_agent import (
    CapabilityTelemetryEvent,
    InMemoryCapabilityTelemetrySink,
    JsonFormatter,
    JsonlCapabilityTelemetrySink,
    MetricsRegistry,
    Observability,
    redact_fields,
    redact_text,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_redaction_bounds_values_and_never_keeps_sensitive_fields() -> None:
    fields = redact_fields(
        {
            "invocation_id": "inv-1",
            "prompt": "do not retain this",
            "authorization": "Bearer secret-token",
            "nested": {"client_secret": "secret", "status": "ok"},
            "long_value": "x" * 300,
        }
    )

    assert fields["invocation_id"] == "inv-1"
    assert fields["prompt"] == "[REDACTED]"
    assert fields["authorization"] == "[REDACTED]"
    assert fields["nested"] == {"client_secret": "[REDACTED]", "status": "ok"}
    assert fields["long_value"] == f"{'x' * 253}..."
    assert redact_text("Authorization: Bearer abc123") == "Authorization: Bearer [REDACTED]"


def test_metrics_are_bounded_and_render_prometheus() -> None:
    metrics = MetricsRegistry(max_series=2)
    metrics.increment("osa_invocations_total", {"agent": "support", "outcome": "success"})
    metrics.increment("osa_invocations_total", {"agent": "support", "outcome": "success"})
    metrics.observe("osa_invocation_duration", 0.25, {"operation": "invoke"})
    metrics.increment("ignored", {"agent": "third-series"})

    rendered = metrics.render_prometheus()
    assert 'osa_invocations_total{agent="support",outcome="success"} 2' in rendered
    assert 'osa_invocation_duration_seconds_count{operation="invoke"} 1' in rendered
    assert "third-series" not in rendered


@pytest.mark.asyncio
async def test_observability_counts_success_and_error() -> None:
    observation = Observability(MetricsRegistry())

    async with observation.span("invoke", labels={"agent": "demo"}):
        pass
    with pytest.raises(RuntimeError, match="boom"):
        async with observation.span("tool", labels={"tool": "calculator"}):
            raise RuntimeError("boom")

    rendered = observation.metrics.render_prometheus()
    assert 'operation="invoke"' in rendered
    assert 'operation="tool"' in rendered
    assert 'outcome="error"' in rendered


@pytest.mark.asyncio
async def test_capability_telemetry_is_bounded_and_payload_free() -> None:
    sink = InMemoryCapabilityTelemetrySink(max_events=2)
    observation = Observability(MetricsRegistry(), capability_sink=sink)

    async with observation.span("model.run", labels={"model": "default"}):
        pass
    with pytest.raises(RuntimeError):
        async with observation.span("tool.execute", labels={"tool": "calculator", "input": "secret"}):
            raise RuntimeError("failed")
    async with observation.span("mcp.call", labels={"server": "partner", "tool": "lookup"}):
        pass

    events = sink.events()
    assert len(events) == 2
    assert events[0] == CapabilityTelemetryEvent("tool", "calculator", "error", None, events[0].duration_seconds)
    assert events[1].kind == "mcp"
    assert "secret" not in repr(events)
    assert "osa_capability_events_total" in observation.metrics.render_prometheus()


@pytest.mark.asyncio
async def test_capability_telemetry_records_timeout_outcome() -> None:
    sink = InMemoryCapabilityTelemetrySink()
    observation = Observability(MetricsRegistry(), capability_sink=sink)

    with pytest.raises(TimeoutError):
        async with observation.span("model.run", labels={"model": "slow"}):
            async with asyncio.timeout(0.001):
                await asyncio.sleep(0.02)

    event = sink.events()[0]
    assert event.kind == "model"
    assert event.name == "slow"
    assert event.outcome == "error"


def test_jsonl_capability_sink_is_bounded_sanitized_and_configurable(tmp_path: Path) -> None:
    path = tmp_path / "telemetry" / "capabilities.jsonl"
    sink = JsonlCapabilityTelemetrySink(path, max_bytes=1024, fsync=False)

    for index in range(20):
        sink.record(
            CapabilityTelemetryEvent(
                kind="tool",
                name=f"tool-{index}",
                outcome="error",
                error_code="tool_failed",
                duration_seconds=0.25,
            )
        )
    sink.record(CapabilityTelemetryEvent("model", "token:secret", "success", None, float("nan")))

    assert path.stat().st_size <= 1024
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records
    assert records[-1]["name"] == "token:[REDACTED]"
    assert records[-1]["duration_seconds"] == 0.0
    assert all(set(record) == {"duration_seconds", "error_code", "kind", "name", "outcome"} for record in records)


def test_capability_sink_from_environment_is_opt_in(tmp_path: Path) -> None:
    from osa.generic_agent.observability import capability_sink_from_env

    assert capability_sink_from_env({}) is None
    sink = capability_sink_from_env(
        {
            "OSA_CAPABILITY_TELEMETRY_PATH": str(tmp_path / "events.jsonl"),
            "OSA_CAPABILITY_TELEMETRY_MAX_BYTES": "2048",
        }
    )
    assert isinstance(sink, JsonlCapabilityTelemetrySink)

    with pytest.raises(ValueError, match="OSA_CAPABILITY_TELEMETRY_MAX_BYTES"):
        capability_sink_from_env(
            {
                "OSA_CAPABILITY_TELEMETRY_PATH": str(tmp_path / "events.jsonl"),
                "OSA_CAPABILITY_TELEMETRY_MAX_BYTES": "bad",
            }
        )


def test_json_formatter_emits_structured_redacted_fields() -> None:
    record = logging.LogRecord("osa.test", logging.INFO, __file__, 1, "completed", (), None)
    record.osa_fields = {"request_id": "req-1", "token": "do-not-log"}
    payload = json.loads(JsonFormatter().format(record))

    assert payload["request_id"] == "req-1"
    assert payload["token"] == "[REDACTED]"
