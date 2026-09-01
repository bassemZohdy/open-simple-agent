"""Tests for redaction-safe structured observability."""

from __future__ import annotations

import json
import logging

import pytest

from osa.generic_agent import JsonFormatter, MetricsRegistry, Observability, redact_fields, redact_text


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


def test_json_formatter_emits_structured_redacted_fields() -> None:
    record = logging.LogRecord("osa.test", logging.INFO, __file__, 1, "completed", (), None)
    record.osa_fields = {"request_id": "req-1", "token": "do-not-log"}
    payload = json.loads(JsonFormatter().format(record))

    assert payload["request_id"] == "req-1"
    assert payload["token"] == "[REDACTED]"
