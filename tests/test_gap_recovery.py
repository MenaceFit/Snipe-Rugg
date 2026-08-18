from __future__ import annotations

from snipe_rugg.core.dedup import InMemoryDeduplicator
from snipe_rugg.core.event_bus import EventBus
from snipe_rugg.ingestion.gap_recovery import GapRecoveryService
from snipe_rugg.ingestion.pipeline import TOPIC_NORMALIZED_EVENT, IngestionPipeline
from tests.helpers import wait_until


class FakeRpc:
    def __init__(self, signatures_by_address):
        self._signatures_by_address = signatures_by_address
        self.calls = []

    async def get_signatures_for_address(self, address, *, before=None, until=None, limit=1000):
        self.calls.append({"address": address, "until": until, "limit": limit})
        return self._signatures_by_address.get(address, [])

    async def get_transaction(self, signature, **kwargs):
        raise NotImplementedError

    async def get_slot(self, **kwargs):
        raise NotImplementedError


async def _make_pipeline():
    bus = EventBus()
    pipeline = IngestionPipeline(deduplicator=InMemoryDeduplicator(), event_bus=bus)
    received = []

    async def on_normalized(event):
        received.append(event)

    bus.subscribe(TOPIC_NORMALIZED_EVENT, on_normalized)
    await pipeline.start()
    return pipeline, received


async def test_recover_replays_missed_signatures_in_chronological_order():
    # getSignaturesForAddress returns newest-first, per the real RPC's documented order.
    rpc = FakeRpc(
        {
            "Wallet111": [
                {"signature": "sig-3", "slot": 103},
                {"signature": "sig-2", "slot": 102},
                {"signature": "sig-1", "slot": 101},
            ]
        }
    )
    pipeline, received = await _make_pipeline()
    try:
        gap_recovery = GapRecoveryService(rpc, pipeline)
        await gap_recovery.recover(["Wallet111"])
        await wait_until(lambda: len(received) == 3)
        assert [e.signature for e in received] == ["sig-1", "sig-2", "sig-3"]
        assert all(e.source == "backfill" for e in received)
    finally:
        await pipeline.stop()


async def test_recover_tracks_last_signature_for_next_call():
    rpc = FakeRpc({"Wallet111": [{"signature": "sig-2", "slot": 102}, {"signature": "sig-1", "slot": 101}]})
    pipeline, _ = await _make_pipeline()
    try:
        gap_recovery = GapRecoveryService(rpc, pipeline)
        await gap_recovery.recover(["Wallet111"])
        assert rpc.calls[0]["until"] is None

        await gap_recovery.recover(["Wallet111"])
        assert rpc.calls[1]["until"] == "sig-2"
    finally:
        await pipeline.stop()


async def test_recover_with_no_missed_signatures_is_a_noop():
    rpc = FakeRpc({})
    pipeline, received = await _make_pipeline()
    try:
        gap_recovery = GapRecoveryService(rpc, pipeline)
        await gap_recovery.recover(["Wallet111"])
        assert received == []
    finally:
        await pipeline.stop()
