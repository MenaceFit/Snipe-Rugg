from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace, NormalizedChainEvent, SubscriptionKind
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.models import WalletSource
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.launchpad.models import LaunchEvent
from snipe_rugg.launchpad.monitor import LAUNCH_FIREHOSE_KEY, LaunchMonitor
from snipe_rugg.tracking.wallet_tracker import token_subscription_key, wallet_subscription_key
from tests.helpers import FakeStreamingProvider, load_fixture

DEV_WALLET = "DevWallet111111111111111111111111111111111"
MINT = "NewPumpMintAccount1111111111111111111111111"


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
        self.launches = []
        self.dev_risk_alerts = []

    async def send(self, *, wallet, event, latency):
        raise NotImplementedError

    async def send_launch(self, *, wallet, launch, latency):
        self.launches.append((wallet, launch))
        return "fake-launch-message-id"

    async def send_graduation(self, *, token, wallet, latency):
        raise NotImplementedError

    async def send_dev_risk(self, *, wallet, assessment, latency):
        self.dev_risk_alerts.append((wallet, assessment))
        return "fake-dev-risk-message-id"


def _chain_event(*, signature: str, subscription_key: str = LAUNCH_FIREHOSE_KEY) -> NormalizedChainEvent:
    return NormalizedChainEvent(
        kind=SubscriptionKind.LOGS,
        provider="solana_ws",
        subscription_key=subscription_key,
        slot=300000005,
        signature=signature,
        err=None,
        latency=LatencyTrace(provider_received_at=utc_now()),
        raw={},
    )


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


def _monitor(session_factory, *, rpc, sink=None, provider=None):
    return LaunchMonitor(
        rpc=rpc,
        provider=provider or FakeStreamingProvider(),
        session_factory=session_factory,
        alert_sink=sink or FakeAlertSink(),
        dev_monitor=DevMonitorService(session_factory),
    )


async def test_subscribe_registers_the_pump_fun_program_id(session_factory):
    provider = FakeStreamingProvider()
    monitor = _monitor(session_factory, rpc=FakeRpc({}), provider=provider)
    await monitor.subscribe()
    assert provider.calls == [
        ("subscribe_logs", (), {"mentions": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"], "commitment": "confirmed", "key": LAUNCH_FIREHOSE_KEY})
    ]


async def test_ignores_events_on_a_different_subscription_key(session_factory):
    sink = FakeAlertSink()
    monitor = _monitor(session_factory, rpc=FakeRpc({}), sink=sink)
    event = _chain_event(signature="sig-x", subscription_key="wallet:SomeoneElse")
    await monitor.handle_normalized_event(event)
    assert sink.launches == []


async def test_unknown_creator_launch_is_recorded_auto_tracked_and_alerted(session_factory):
    raw_tx = load_fixture("tx_pumpfun_launch.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    provider = FakeStreamingProvider()
    monitor = _monitor(session_factory, rpc=rpc, sink=sink, provider=provider)

    await monitor.handle_normalized_event(_chain_event(signature=signature))

    assert len(sink.launches) == 1
    wallet, launch = sink.launches[0]
    assert wallet.address == DEV_WALLET
    assert wallet.source == WalletSource.AUTO_DEV.value
    assert launch.mint == MINT

    subscribe_calls = {c[2]["key"] for c in provider.calls if c[0] == "subscribe_logs"}
    assert subscribe_calls == {token_subscription_key(MINT), wallet_subscription_key(DEV_WALLET)}

    async with session_factory() as session:
        repo = WalletRepository(session)
        assert await repo.get_token(MINT) is not None
        tracked = await repo.get_wallet(DEV_WALLET)
        assert tracked is not None
        assert tracked.source == WalletSource.AUTO_DEV.value


async def test_already_manually_tracked_creator_is_not_resubscribed_as_a_wallet(session_factory):
    async with session_factory() as session:
        await WalletRepository(session).add_wallet(DEV_WALLET, name="Manually Tracked")
        await session.commit()

    raw_tx = load_fixture("tx_pumpfun_launch.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    provider = FakeStreamingProvider()
    monitor = _monitor(session_factory, rpc=rpc, provider=provider)

    await monitor.handle_normalized_event(_chain_event(signature=signature))

    subscribe_calls = [c for c in provider.calls if c[0] == "subscribe_logs"]
    assert len(subscribe_calls) == 1  # only the mint, not a redundant wallet subscribe
    assert subscribe_calls[0][2]["key"] == token_subscription_key(MINT)


async def test_launch_already_recorded_elsewhere_is_a_noop(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.record_token_launch(
            LaunchEvent(
                mint=MINT, creator=DEV_WALLET, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None,
                signature="sig-already-seen",
            )
        )
        await session.commit()

    raw_tx = load_fixture("tx_pumpfun_launch.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    provider = FakeStreamingProvider()
    monitor = _monitor(session_factory, rpc=rpc, sink=sink, provider=provider)

    await monitor.handle_normalized_event(_chain_event(signature=signature))

    assert sink.launches == []
    assert not any(c[0] == "subscribe_logs" for c in provider.calls)
