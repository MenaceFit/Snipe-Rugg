"""Provider-agnostic data contracts for the real-time ingestion core.

These are the Phase 1 "normalization" targets: raw, provider-specific WebSocket
notifications (Solana native or Helius Enhanced — same wire protocol) are converted
into `NormalizedChainEvent` before anything downstream touches them. Business-level
events (BUY/SELL/TOKEN_CREATE/...) are a Phase 2 concern produced by the transaction
decoder, which doesn't exist yet — its output contracts belong next to it, not here.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from snipe_rugg.core.clock import utc_now


class SubscriptionKind(StrEnum):
    LOGS = "logs"
    ACCOUNT = "account"
    PROGRAM = "program"
    SLOT = "slot"
    SIGNATURE = "signature"


class RawStreamMessage(BaseModel):
    """A single WebSocket notification exactly as received, before normalization."""

    model_config = ConfigDict(frozen=True)

    provider: str
    method: str
    subscription_key: str
    payload: dict[str, Any]
    slot: int | None = None
    received_at: datetime = Field(default_factory=utc_now)


class LatencyTrace(BaseModel):
    """Per-stage timestamps for one event's trip through the pipeline.

    Every stage past `provider_received_at` is optional and left unset until it
    actually happens: latency numbers are measured, never invented (spec section 4/25).
    Each *_latency_ms property returns None rather than a fabricated number when its
    inputs aren't both known yet.
    """

    chain_slot: int | None = None
    block_time: datetime | None = None
    provider_received_at: datetime
    ingested_at: datetime | None = None
    decoded_at: datetime | None = None
    classified_at: datetime | None = None
    persisted_at: datetime | None = None
    alerted_at: datetime | None = None

    @staticmethod
    def _delta_ms(start: datetime | None, end: datetime | None) -> float | None:
        if start is None or end is None:
            return None
        return (end - start).total_seconds() * 1000.0

    @property
    def ingestion_latency_ms(self) -> float | None:
        return self._delta_ms(self.provider_received_at, self.ingested_at)

    @property
    def decode_latency_ms(self) -> float | None:
        return self._delta_ms(self.ingested_at, self.decoded_at)

    @property
    def classification_latency_ms(self) -> float | None:
        return self._delta_ms(self.decoded_at, self.classified_at)

    @property
    def persistence_latency_ms(self) -> float | None:
        return self._delta_ms(self.classified_at, self.persisted_at)

    @property
    def notification_latency_ms(self) -> float | None:
        return self._delta_ms(self.persisted_at or self.classified_at, self.alerted_at)

    @property
    def detection_latency_ms(self) -> float | None:
        """Provider reception -> normalized & queued. The 'Detection latency' shown on alerts."""
        return self.ingestion_latency_ms

    @property
    def total_latency_ms(self) -> float | None:
        return self._delta_ms(self.provider_received_at, self.alerted_at)


class NormalizedChainEvent(BaseModel):
    """Provider-agnostic normalized form of any subscription notification."""

    kind: SubscriptionKind
    provider: str
    subscription_key: str
    slot: int | None = None
    signature: str | None = None
    err: Any | None = None
    accounts: list[str] = Field(default_factory=list)
    logs: list[str] | None = None
    account_data: dict[str, Any] | None = None
    source: Literal["live", "backfill"] = "live"
    latency: LatencyTrace
    raw: dict[str, Any]
