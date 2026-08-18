from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace, NormalizedChainEvent, SubscriptionKind
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.models import TokenTrade, WalletStatus
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.tracking.wallet_tracker import (
    WalletTracker,
    wallet_address_from_key,
    wallet_subscription_key,
)
from tests.helpers import load_fixture


class FakeRpc:
    def __init__(self, tx_by_signature):
        self._tx_by_signature = tx_by_signature

    async def get_transaction(self, signature, **kwargs):
        return self._tx_by_signature.get(signature)

    async def get_signatures_for_address(self, *args, **kwargs):
        raise NotImplementedError

    async def get_slot(self, **kwargs):
        raise NotImplementedError


class FakeAlertSink:
    def __init__(self):
        self.sent = []

    async def send(self, *, wallet, event, latency):
        self.sent.append((wallet, event))
        return "fake-message-id"


def _chain_event(*, signature: str, subscription_key: str, err=None) -> NormalizedChainEvent:
    return NormalizedChainEvent(
        kind=SubscriptionKind.LOGS,
        provider="solana_ws",
        subscription_key=subscription_key,
        slot=300000001,
        signature=signature,
        err=err,
        latency=LatencyTrace(provider_received_at=utc_now()),
        raw={},
    )


@pytest.fixture
async def session_factory():
    # StaticPool: WalletTracker opens a fresh session per DB interaction (by
    # design - see the module docstring), and a plain sqlite in-memory DB gives
    # each new connection its own empty database, which would silently break
    # these tests (e.g. a wallet added in one session invisible to the next).
    # StaticPool keeps every checkout on the one connection so state persists
    # across the separate session_factory() calls below, matching how the real
    # postgres engine behaves without needing this pool.
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


def test_wallet_subscription_key_roundtrip():
    key = wallet_subscription_key("Wallet111")
    assert key == "wallet:Wallet111"
    assert wallet_address_from_key(key) == "Wallet111"
    assert wallet_address_from_key("logs:all") is None


async def test_buy_is_persisted_and_alerted(session_factory):
    wallet_address = "WalletBuyer1111111111111111111111111111111"
    async with session_factory() as session:
        await WalletRepository(session).add_wallet(wallet_address, name="DEV_ORANGE")
        await session.commit()

    raw_tx = load_fixture("tx_buy_pumpswap.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    sink = FakeAlertSink()
    tracker = WalletTracker(rpc=rpc, session_factory=session_factory, alert_sink=sink)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0], subscription_key=wallet_subscription_key(wallet_address)
    )
    await tracker.handle_normalized_event(event)

    assert len(sink.sent) == 1
    sent_wallet, sent_event = sink.sent[0]
    assert sent_wallet.address == wallet_address
    assert isinstance(sent_event, NormalizedTrade)
    assert sent_event.side is EventType.BUY

    assert event.latency.decoded_at is not None
    assert event.latency.classified_at is not None
    assert event.latency.persisted_at is not None
    assert event.latency.alerted_at is not None


async def test_untracked_wallet_is_ignored(session_factory):
    raw_tx = load_fixture("tx_buy_pumpswap.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    sink = FakeAlertSink()
    tracker = WalletTracker(rpc=rpc, session_factory=session_factory, alert_sink=sink)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0],
        subscription_key=wallet_subscription_key("SomeUntrackedWallet1111111111111111111111111"),
    )
    await tracker.handle_normalized_event(event)

    assert sink.sent == []


async def test_paused_wallet_is_not_alerted(session_factory):
    wallet_address = "WalletBuyer1111111111111111111111111111111"
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.add_wallet(wallet_address)
        await repo.set_status(wallet_address, WalletStatus.PAUSED)
        await session.commit()

    raw_tx = load_fixture("tx_buy_pumpswap.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    sink = FakeAlertSink()
    tracker = WalletTracker(rpc=rpc, session_factory=session_factory, alert_sink=sink)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0], subscription_key=wallet_subscription_key(wallet_address)
    )
    await tracker.handle_normalized_event(event)

    assert sink.sent == []


async def test_alert_buys_false_suppresses_buy_alert(session_factory):
    wallet_address = "WalletBuyer1111111111111111111111111111111"
    async with session_factory() as session:
        repo = WalletRepository(session)
        wallet = await repo.add_wallet(wallet_address)
        wallet.alert_buys = False
        await session.commit()

    raw_tx = load_fixture("tx_buy_pumpswap.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    sink = FakeAlertSink()
    tracker = WalletTracker(rpc=rpc, session_factory=session_factory, alert_sink=sink)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0], subscription_key=wallet_subscription_key(wallet_address)
    )
    await tracker.handle_normalized_event(event)

    assert sink.sent == []

    async with session_factory() as session:
        result = await session.execute(select(TokenTrade).where(TokenTrade.wallet_address == wallet_address))
        assert result.scalar_one_or_none() is not None  # still persisted, just not alerted


async def test_failed_transaction_signature_is_skipped(session_factory):
    wallet_address = "WalletBuyer1111111111111111111111111111111"
    async with session_factory() as session:
        await WalletRepository(session).add_wallet(wallet_address)
        await session.commit()

    sink = FakeAlertSink()
    rpc = FakeRpc({})
    tracker = WalletTracker(rpc=rpc, session_factory=session_factory, alert_sink=sink)

    event = _chain_event(
        signature="sig-failed",
        subscription_key=wallet_subscription_key(wallet_address),
        err={"InstructionError": [0, "Custom"]},
    )
    await tracker.handle_normalized_event(event)

    assert sink.sent == []
