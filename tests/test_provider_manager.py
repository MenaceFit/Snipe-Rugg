from __future__ import annotations

import pytest

from snipe_rugg.providers.base import ConnectionState
from snipe_rugg.providers.manager import ProviderManager
from tests.helpers import FakeStreamingProvider


async def test_subscribe_logs_fans_out_to_every_provider():
    a, b = FakeStreamingProvider("a"), FakeStreamingProvider("b")
    manager = ProviderManager([a, b])

    async def on_event(msg):
        pass

    manager.set_event_handler(on_event)
    await manager.start()

    key = await manager.subscribe_logs(mentions=["Wallet111"])

    assert key
    assert a.calls[0][0] == "subscribe_logs"
    assert b.calls[0][0] == "subscribe_logs"
    assert a._on_event is on_event
    assert b._on_event is on_event


async def test_state_is_connected_when_all_healthy_and_degraded_when_partial():
    a, b = FakeStreamingProvider("a"), FakeStreamingProvider("b")
    manager = ProviderManager([a, b])
    manager.set_event_handler(lambda msg: None)

    assert manager.state == ConnectionState.DISCONNECTED

    await a.start()
    assert manager.state == ConnectionState.DEGRADED

    await b.start()
    assert manager.state == ConnectionState.CONNECTED


async def test_fan_out_tolerates_partial_subscribe_failure():
    a, b = FakeStreamingProvider("a"), FakeStreamingProvider("b")
    a.fail_next_subscribe = True
    manager = ProviderManager([a, b])
    manager.set_event_handler(lambda msg: None)

    key = await manager.subscribe_slot()  # must not raise: b succeeded

    assert key
    assert a.calls[0][0] == "subscribe_slot"
    assert b.calls[0][0] == "subscribe_slot"


async def test_fan_out_raises_when_every_provider_fails():
    a, b = FakeStreamingProvider("a"), FakeStreamingProvider("b")
    a.fail_next_subscribe = True
    b.fail_next_subscribe = True
    manager = ProviderManager([a, b])
    manager.set_event_handler(lambda msg: None)

    with pytest.raises(RuntimeError):
        await manager.subscribe_slot()


async def test_provider_statuses_reports_each_provider_by_name():
    a, b = FakeStreamingProvider("a"), FakeStreamingProvider("b")
    manager = ProviderManager([a, b])
    manager.set_event_handler(lambda msg: None)
    await a.start()

    statuses = manager.provider_statuses()
    assert statuses == {"a": ConnectionState.CONNECTED, "b": ConnectionState.DISCONNECTED}
