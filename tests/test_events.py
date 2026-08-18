from __future__ import annotations

from datetime import timedelta

import pytest

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace


def test_latencies_are_none_until_both_endpoints_known():
    trace = LatencyTrace(provider_received_at=utc_now())
    assert trace.ingestion_latency_ms is None
    assert trace.decode_latency_ms is None
    assert trace.total_latency_ms is None


def test_latency_ms_computed_when_both_endpoints_present():
    start = utc_now()
    trace = LatencyTrace(
        provider_received_at=start,
        ingested_at=start + timedelta(milliseconds=50),
        decoded_at=start + timedelta(milliseconds=62),
        alerted_at=start + timedelta(milliseconds=180),
    )
    assert trace.ingestion_latency_ms == pytest.approx(50, abs=0.5)
    assert trace.decode_latency_ms == pytest.approx(12, abs=0.5)
    assert trace.detection_latency_ms == pytest.approx(50, abs=0.5)
    assert trace.total_latency_ms == pytest.approx(180, abs=0.5)
