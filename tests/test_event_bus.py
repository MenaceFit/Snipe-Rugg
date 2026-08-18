from __future__ import annotations

from snipe_rugg.core.event_bus import EventBus


async def test_publish_delivers_to_all_subscribers():
    bus = EventBus()
    received_a = []
    received_b = []

    async def handler_a(payload):
        received_a.append(payload)

    async def handler_b(payload):
        received_b.append(payload)

    bus.subscribe("topic", handler_a)
    bus.subscribe("topic", handler_b)

    await bus.publish("topic", {"x": 1})

    assert received_a == [{"x": 1}]
    assert received_b == [{"x": 1}]


async def test_publish_with_no_subscribers_is_a_noop():
    bus = EventBus()
    await bus.publish("nobody-listening", {"x": 1})  # must not raise


async def test_one_handler_raising_does_not_stop_the_others():
    bus = EventBus()
    received = []

    async def broken_handler(payload):
        raise RuntimeError("boom")

    async def healthy_handler(payload):
        received.append(payload)

    bus.subscribe("topic", broken_handler)
    bus.subscribe("topic", healthy_handler)

    await bus.publish("topic", {"x": 1})  # must not raise despite broken_handler

    assert received == [{"x": 1}]


async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received = []

    async def handler(payload):
        received.append(payload)

    bus.subscribe("topic", handler)
    bus.unsubscribe("topic", handler)
    await bus.publish("topic", {"x": 1})

    assert received == []
    assert bus.subscriber_count("topic") == 0
