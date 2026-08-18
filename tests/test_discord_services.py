from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool

from snipe_rugg.db.base import create_engine, create_session_factory, init_models
from snipe_rugg.discord_bot.services import WalletCommandService, WatchlistCommandService
from snipe_rugg.tracking.wallet_tracker import wallet_subscription_key
from tests.helpers import FakeStreamingProvider


@pytest.fixture
async def session_factory():
    engine = create_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    await init_models(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.fixture
def provider():
    return FakeStreamingProvider()


async def test_add_wallet_persists_and_subscribes(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)

    reply = await service.add_wallet("Wallet111", "DEV_ORANGE")

    assert "✅" in reply
    assert "DEV_ORANGE" in reply
    assert provider.calls == [
        ("subscribe_logs", (), {"mentions": ["Wallet111"], "commitment": "confirmed", "key": wallet_subscription_key("Wallet111")})
    ]


async def test_add_duplicate_wallet_is_a_friendly_message(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111")

    reply = await service.add_wallet("Wallet111")

    assert "already tracked" in reply
    assert len(provider.calls) == 1  # no second subscribe attempt


async def test_remove_wallet_unsubscribes(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111")

    reply = await service.remove_wallet("Wallet111")

    assert "✅" in reply
    assert provider.calls[-1] == ("unsubscribe", (wallet_subscription_key("Wallet111"),), {})


async def test_remove_unknown_wallet_is_a_friendly_message(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    reply = await service.remove_wallet("Ghost111")
    assert "not tracked" in reply


async def test_pause_unsubscribes_and_resume_resubscribes(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111")

    await service.pause_wallet("Wallet111")
    assert provider.calls[-1][0] == "unsubscribe"

    await service.resume_wallet("Wallet111")
    assert provider.calls[-1][0] == "subscribe_logs"


async def test_list_wallets_shows_status_markers(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111", "DEV_ORANGE")
    await service.add_wallet("Wallet222", "DEV_BLUE")
    await service.pause_wallet("Wallet222")

    reply = await service.list_wallets()

    assert "🟢 DEV_ORANGE" in reply
    assert "⏸️ DEV_BLUE" in reply


async def test_wallet_info_reports_status(session_factory, provider):
    service = WalletCommandService(session_factory=session_factory, provider=provider)
    await service.add_wallet("Wallet111", "DEV_ORANGE")

    reply = await service.wallet_info("Wallet111")

    assert "DEV_ORANGE" in reply
    assert "ACTIVE" in reply


async def test_watchlist_create_and_add_wallet(session_factory, provider):
    wallet_service = WalletCommandService(session_factory=session_factory, provider=provider)
    watchlist_service = WatchlistCommandService(session_factory=session_factory)
    await wallet_service.add_wallet("Wallet111", "DEV_ORANGE")

    create_reply = await watchlist_service.create_watchlist("RUGGERS")
    assert "✅" in create_reply

    add_reply = await watchlist_service.add_wallet("RUGGERS", "Wallet111")
    assert "✅" in add_reply

    listing = await watchlist_service.list_watchlist("RUGGERS")
    assert "DEV_ORANGE" in listing


async def test_watchlist_add_wallet_to_unknown_watchlist(session_factory, provider):
    wallet_service = WalletCommandService(session_factory=session_factory, provider=provider)
    watchlist_service = WatchlistCommandService(session_factory=session_factory)
    await wallet_service.add_wallet("Wallet111")

    reply = await watchlist_service.add_wallet("NOPE", "Wallet111")
    assert "does not exist" in reply


async def test_watchlist_add_unknown_wallet(session_factory):
    watchlist_service = WatchlistCommandService(session_factory=session_factory)
    await watchlist_service.create_watchlist("RUGGERS")

    reply = await watchlist_service.add_wallet("RUGGERS", "GhostWallet111")
    assert "not tracked yet" in reply
