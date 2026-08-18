from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.core.clock import utc_now
from snipe_rugg.core.events import LatencyTrace, NormalizedChainEvent, SubscriptionKind
from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.db.repository import WalletRepository
from snipe_rugg.dev.service import DevMonitorService
from snipe_rugg.launchpad.models import LaunchEvent, LaunchpadStatus
from snipe_rugg.tracking.token_tracker import TokenTracker
from snipe_rugg.tracking.wallet_tracker import token_mint_from_key, token_subscription_key
from tests.helpers import load_fixture

MINT = "NewPumpMintAccount1111111111111111111111111"
DEV_WALLET = "DevWallet111111111111111111111111111111111"


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
        self.graduations = []
        self.dev_risk_alerts = []

    async def send(self, *, wallet, event, latency):
        raise NotImplementedError

    async def send_launch(self, *, wallet, launch, latency):
        raise NotImplementedError

    async def send_graduation(self, *, token, wallet, latency):
        self.graduations.append((token, wallet))
        return "fake-graduation-message-id"

    async def send_dev_risk(self, *, wallet, assessment, latency):
        self.dev_risk_alerts.append((wallet, assessment))
        return "fake-dev-risk-message-id"


def _chain_event(*, signature: str, subscription_key: str) -> NormalizedChainEvent:
    return NormalizedChainEvent(
        kind=SubscriptionKind.LOGS,
        provider="solana_ws",
        subscription_key=subscription_key,
        slot=300000006,
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


def test_token_subscription_key_roundtrip():
    key = token_subscription_key(MINT)
    assert key == f"token:{MINT}"
    assert token_mint_from_key(key) == MINT
    assert token_mint_from_key("wallet:x") is None


async def test_graduation_updates_status_and_alerts(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.add_wallet(DEV_WALLET, name="DEV_ORANGE")
        await repo.record_token_launch(
            LaunchEvent(mint=MINT, creator=DEV_WALLET, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
        )
        await session.commit()

    raw_tx = load_fixture("tx_pumpfun_graduation.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    tracker = TokenTracker(
        rpc=rpc, session_factory=session_factory, alert_sink=sink, dev_monitor=DevMonitorService(session_factory)
    )

    event = _chain_event(signature=signature, subscription_key=token_subscription_key(MINT))
    await tracker.handle_normalized_event(event)

    assert len(sink.graduations) == 1
    token, wallet = sink.graduations[0]
    assert token.status == LaunchpadStatus.GRADUATED.value
    assert token.graduated_signature == signature
    assert token.graduated_slot == 300000006
    assert wallet is not None
    assert wallet.address == DEV_WALLET

    async with session_factory() as session:
        persisted = await WalletRepository(session).get_token(MINT)
        assert persisted is not None
        assert persisted.status == LaunchpadStatus.GRADUATED.value


async def test_graduation_alert_suppressed_when_creator_disabled_launch_alerts(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        wallet = await repo.add_wallet(DEV_WALLET)
        wallet.alert_launches = False
        await repo.record_token_launch(
            LaunchEvent(mint=MINT, creator=DEV_WALLET, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
        )
        await session.commit()

    raw_tx = load_fixture("tx_pumpfun_graduation.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    tracker = TokenTracker(
        rpc=rpc, session_factory=session_factory, alert_sink=sink, dev_monitor=DevMonitorService(session_factory)
    )

    event = _chain_event(signature=signature, subscription_key=token_subscription_key(MINT))
    await tracker.handle_normalized_event(event)

    assert sink.graduations == []
    # still marked graduated in the DB even though the alert was suppressed
    async with session_factory() as session:
        persisted = await WalletRepository(session).get_token(MINT)
        assert persisted is not None
        assert persisted.status == LaunchpadStatus.GRADUATED.value


async def test_graduation_without_a_known_creator_wallet_still_alerts(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.record_token_launch(
            LaunchEvent(mint=MINT, creator=DEV_WALLET, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
        )
        await session.commit()  # creator wallet was never separately tracked / was removed

    raw_tx = load_fixture("tx_pumpfun_graduation.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    tracker = TokenTracker(
        rpc=rpc, session_factory=session_factory, alert_sink=sink, dev_monitor=DevMonitorService(session_factory)
    )

    event = _chain_event(signature=signature, subscription_key=token_subscription_key(MINT))
    await tracker.handle_normalized_event(event)

    assert len(sink.graduations) == 1
    _, wallet = sink.graduations[0]
    assert wallet is None


async def test_unknown_mint_is_ignored(session_factory):
    raw_tx = load_fixture("tx_pumpfun_graduation.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    tracker = TokenTracker(
        rpc=rpc, session_factory=session_factory, alert_sink=sink, dev_monitor=DevMonitorService(session_factory)
    )

    event = _chain_event(signature=signature, subscription_key=token_subscription_key("GhostMint111"))
    await tracker.handle_normalized_event(event)

    assert sink.graduations == []


async def test_already_graduated_token_is_not_reprocessed(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.record_token_launch(
            LaunchEvent(mint=MINT, creator=DEV_WALLET, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
        )
        await repo.mark_graduated(MINT, slot=1, block_time=None, signature="sig-first-graduation")
        await session.commit()

    raw_tx = load_fixture("tx_pumpfun_graduation.json")
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    tracker = TokenTracker(
        rpc=rpc, session_factory=session_factory, alert_sink=sink, dev_monitor=DevMonitorService(session_factory)
    )

    event = _chain_event(signature=signature, subscription_key=token_subscription_key(MINT))
    await tracker.handle_normalized_event(event)

    assert sink.graduations == []


async def test_non_graduation_transaction_on_watched_mint_is_ignored(session_factory):
    async with session_factory() as session:
        repo = WalletRepository(session)
        await repo.record_token_launch(
            LaunchEvent(mint=MINT, creator=DEV_WALLET, launchpad="Pump.fun", pair="SOL", slot=1, block_time=None, signature="sig-launch")
        )
        await session.commit()

    raw_tx = load_fixture("tx_buy_pumpswap.json")  # touches PumpSwap but not Pump.fun
    signature = raw_tx["transaction"]["signatures"][0]
    rpc = FakeRpc({signature: raw_tx})
    sink = FakeAlertSink()
    tracker = TokenTracker(
        rpc=rpc, session_factory=session_factory, alert_sink=sink, dev_monitor=DevMonitorService(session_factory)
    )

    event = _chain_event(signature=signature, subscription_key=token_subscription_key(MINT))
    await tracker.handle_normalized_event(event)

    assert sink.graduations == []
    async with session_factory() as session:
        persisted = await WalletRepository(session).get_token(MINT)
        assert persisted is not None
        assert persisted.status == LaunchpadStatus.BONDING_CURVE.value
