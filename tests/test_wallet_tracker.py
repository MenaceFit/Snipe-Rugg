from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.event_bus import EventBus
from snipe_rugg.core.events import LatencyTrace, NormalizedChainEvent, SubscriptionKind
from snipe_rugg.core.topics import TOPIC_NEW_TRADE
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.models import TokenTrade, WalletStatus
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.decoder.models import EventType, NormalizedTrade
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.tracking.wallet_tracker import (
    WalletTracker,
    token_subscription_key,
    wallet_address_from_key,
    wallet_subscription_key,
)
from tests.helpers import FakeStreamingProvider, load_fixture


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
        self.launches = []
        self.graduations = []
        self.dev_risk_alerts = []

    async def send(self, *, wallet, event, latency):
        self.sent.append((wallet, event))
        return "fake-message-id"

    async def send_launch(self, *, wallet, launch, latency):
        self.launches.append((wallet, launch))
        return "fake-launch-message-id"

    async def send_graduation(self, *, token, wallet, latency):
        self.graduations.append((token, wallet))
        return "fake-graduation-message-id"

    async def send_dev_risk(self, *, wallet, assessment, latency):
        self.dev_risk_alerts.append((wallet, assessment))
        return "fake-dev-risk-message-id"


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


def _tracker(session_factory, *, rpc, sink=None, provider=None, dev_monitor=None, bus=None):
    return WalletTracker(
        rpc=rpc,
        provider=provider or FakeStreamingProvider(),
        session_factory=session_factory,
        alert_sink=sink or FakeAlertSink(),
        dev_monitor=dev_monitor or DevMonitorService(session_factory),
        bus=bus or EventBus(),
    )


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
    tracker = _tracker(session_factory, rpc=rpc, sink=sink)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0], subscription_key=wallet_subscription_key(wallet_address)
    )
    await tracker.handle_normalized_event(event)

    assert len(sink.sent) == 1
    sent_wallet, sent_event = sink.sent[0]
    assert sent_wallet.address == wallet_address
    assert isinstance(sent_event, NormalizedTrade)
    assert sent_event.side is EventType.BUY


async def test_buy_is_published_to_the_new_trade_topic_for_the_strategy_engine(session_factory):
    wallet_address = "WalletBuyer1111111111111111111111111111111"
    async with session_factory() as session:
        await WalletRepository(session).add_wallet(wallet_address)
        await session.commit()

    raw_tx = load_fixture("tx_buy_pumpswap.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    bus = EventBus()
    published = []

    async def record(trade):
        published.append(trade)

    bus.subscribe(TOPIC_NEW_TRADE, record)
    tracker = _tracker(session_factory, rpc=rpc, bus=bus)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0], subscription_key=wallet_subscription_key(wallet_address)
    )
    await tracker.handle_normalized_event(event)

    assert len(published) == 1
    assert published[0].side is EventType.BUY

    assert event.latency.decoded_at is not None
    assert event.latency.classified_at is not None
    assert event.latency.persisted_at is not None
    assert event.latency.alerted_at is not None


async def test_untracked_wallet_is_ignored(session_factory):
    raw_tx = load_fixture("tx_buy_pumpswap.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    sink = FakeAlertSink()
    tracker = _tracker(session_factory, rpc=rpc, sink=sink)

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
    tracker = _tracker(session_factory, rpc=rpc, sink=sink)

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
    tracker = _tracker(session_factory, rpc=rpc, sink=sink)

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
    tracker = _tracker(session_factory, rpc=rpc, sink=sink)

    event = _chain_event(
        signature="sig-failed",
        subscription_key=wallet_subscription_key(wallet_address),
        err={"InstructionError": [0, "Custom"]},
    )
    await tracker.handle_normalized_event(event)

    assert sink.sent == []


async def test_pumpfun_launch_is_persisted_alerted_and_auto_subscribed(session_factory):
    dev_wallet = "DevWallet111111111111111111111111111111111"
    async with session_factory() as session:
        await WalletRepository(session).add_wallet(dev_wallet, name="DEV_ORANGE")
        await session.commit()

    raw_tx = load_fixture("tx_pumpfun_launch.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    sink = FakeAlertSink()
    provider = FakeStreamingProvider()
    tracker = _tracker(session_factory, rpc=rpc, sink=sink, provider=provider)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0], subscription_key=wallet_subscription_key(dev_wallet)
    )
    await tracker.handle_normalized_event(event)

    assert len(sink.launches) == 1
    launch_wallet, launch = sink.launches[0]
    assert launch_wallet.address == dev_wallet
    assert launch.mint == "NewPumpMintAccount1111111111111111111111111"
    assert launch.launchpad == "Pump.fun"

    auto_subscribe_calls = [c for c in provider.calls if c[0] == "subscribe_logs"]
    assert auto_subscribe_calls == [
        (
            "subscribe_logs",
            (),
            {
                "mentions": [launch.mint],
                "commitment": "confirmed",
                "key": token_subscription_key(launch.mint),
            },
        )
    ]

    async with session_factory() as session:
        token = await WalletRepository(session).get_token(launch.mint)
        assert token is not None
        assert token.creator_address == dev_wallet


async def test_launch_not_alerted_when_wallet_disables_launch_alerts(session_factory):
    dev_wallet = "DevWallet111111111111111111111111111111111"
    async with session_factory() as session:
        repo = WalletRepository(session)
        wallet = await repo.add_wallet(dev_wallet)
        wallet.alert_launches = False
        await session.commit()

    raw_tx = load_fixture("tx_pumpfun_launch.json")
    rpc = FakeRpc({raw_tx["transaction"]["signatures"][0]: raw_tx})
    sink = FakeAlertSink()
    provider = FakeStreamingProvider()
    tracker = _tracker(session_factory, rpc=rpc, sink=sink, provider=provider)

    event = _chain_event(
        signature=raw_tx["transaction"]["signatures"][0], subscription_key=wallet_subscription_key(dev_wallet)
    )
    await tracker.handle_normalized_event(event)

    assert sink.launches == []
    # still watched for graduation even though the alert itself was suppressed
    assert any(c[0] == "subscribe_logs" for c in provider.calls)
    async with session_factory() as session:
        assert await WalletRepository(session).get_token("NewPumpMintAccount1111111111111111111111111") is not None
