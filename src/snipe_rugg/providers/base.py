"""Abstract provider interfaces (spec section 100).

Only the interfaces Phase 1 actually needs and implements are defined here:
BlockchainProvider (lifecycle), StreamingProvider (subscriptions), TransactionProvider
(point lookups used for gap recovery). MarketDataProvider, TokenProvider and
ExecutionProvider belong to later phases (token discovery, and paper/live execution
respectively — the latter explicitly last, per spec section 50/151 phase 8) and will
be introduced alongside their first real implementation, not as empty shells now.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from snipe_rugg.core.events import RawStreamMessage

EventHandler = Callable[[RawStreamMessage], Awaitable[None]]
ReconnectListener = Callable[["StreamingProvider"], Awaitable[None]]


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DEGRADED = "degraded"


class BlockchainProvider(ABC):
    name: str

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @property
    @abstractmethod
    def state(self) -> ConnectionState: ...


class StreamingProvider(BlockchainProvider):
    """A source of live subscription notifications (logs/account/program/slot/signature)."""

    @abstractmethod
    def set_event_handler(self, handler: EventHandler) -> None: ...

    @abstractmethod
    def add_reconnect_listener(self, listener: ReconnectListener) -> None: ...

    @property
    @abstractmethod
    def last_seen_slot(self) -> int | None: ...

    @abstractmethod
    async def subscribe_logs(
        self, *, mentions: list[str] | None = None, commitment: str = "confirmed", key: str | None = None
    ) -> str:
        """Subscribe to transaction logs. NOTE: the `mentions` filter is only reliable
        with exactly one address — track many wallets as many subscriptions (they are
        multiplexed over the same physical connection), not one subscription with many
        mentions."""
        ...

    @abstractmethod
    async def subscribe_account(
        self, pubkey: str, *, commitment: str = "finalized", encoding: str = "jsonParsed", key: str | None = None
    ) -> str: ...

    @abstractmethod
    async def subscribe_program(
        self,
        program_id: str,
        *,
        commitment: str = "finalized",
        encoding: str = "base64",
        filters: list[dict[str, Any]] | None = None,
        key: str | None = None,
    ) -> str: ...

    @abstractmethod
    async def subscribe_slot(self, *, key: str | None = None) -> str: ...

    @abstractmethod
    async def subscribe_signature(
        self, signature: str, *, commitment: str = "confirmed", key: str | None = None
    ) -> str: ...

    @abstractmethod
    async def unsubscribe(self, key: str) -> None: ...


class TransactionProvider(ABC):
    """Point lookups against RPC HTTP — used for gap recovery / backfill, not the hot path."""

    @abstractmethod
    async def get_signatures_for_address(
        self, address: str, *, before: str | None = None, until: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_transaction(
        self, signature: str, *, encoding: str = "jsonParsed", max_supported_transaction_version: int = 0
    ) -> dict[str, Any] | None: ...

    @abstractmethod
    async def get_slot(self, *, commitment: str = "finalized") -> int: ...
