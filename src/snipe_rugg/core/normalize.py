"""Converts provider-specific raw notifications into NormalizedChainEvent.

Solana native and Helius Enhanced WebSockets speak the same JSON-RPC subscription
protocol, so one set of normalizers covers both providers — that's the reason to
normalize here rather than downstream in every consumer.
"""
from __future__ import annotations

from snipe_rugg.core.events import (
    LatencyTrace,
    NormalizedChainEvent,
    RawStreamMessage,
    SubscriptionKind,
)

_KIND_BY_METHOD: dict[str, SubscriptionKind] = {
    "logsNotification": SubscriptionKind.LOGS,
    "accountNotification": SubscriptionKind.ACCOUNT,
    "programNotification": SubscriptionKind.PROGRAM,
    "slotNotification": SubscriptionKind.SLOT,
    "signatureNotification": SubscriptionKind.SIGNATURE,
}


class UnsupportedNotification(ValueError):
    pass


def normalize(msg: RawStreamMessage) -> NormalizedChainEvent:
    kind = _KIND_BY_METHOD.get(msg.method)
    if kind is None:
        raise UnsupportedNotification(msg.method)

    latency = LatencyTrace(provider_received_at=msg.received_at, chain_slot=msg.slot)

    if kind is SubscriptionKind.LOGS:
        value = msg.payload.get("value", {})
        return NormalizedChainEvent(
            kind=kind,
            provider=msg.provider,
            subscription_key=msg.subscription_key,
            slot=msg.slot,
            signature=value.get("signature"),
            err=value.get("err"),
            logs=value.get("logs"),
            latency=latency,
            raw=msg.payload,
        )
    if kind is SubscriptionKind.ACCOUNT:
        value = msg.payload.get("value", {})
        return NormalizedChainEvent(
            kind=kind,
            provider=msg.provider,
            subscription_key=msg.subscription_key,
            slot=msg.slot,
            account_data=value,
            latency=latency,
            raw=msg.payload,
        )
    if kind is SubscriptionKind.PROGRAM:
        value = msg.payload.get("value", {})
        pubkey = value.get("pubkey")
        return NormalizedChainEvent(
            kind=kind,
            provider=msg.provider,
            subscription_key=msg.subscription_key,
            slot=msg.slot,
            accounts=[pubkey] if pubkey else [],
            account_data=value.get("account"),
            latency=latency,
            raw=msg.payload,
        )
    if kind is SubscriptionKind.SLOT:
        return NormalizedChainEvent(
            kind=kind,
            provider=msg.provider,
            subscription_key=msg.subscription_key,
            slot=msg.payload.get("slot"),
            latency=latency,
            raw=msg.payload,
        )
    if kind is SubscriptionKind.SIGNATURE:
        value = msg.payload.get("value", {})
        err = value.get("err") if isinstance(value, dict) else None
        return NormalizedChainEvent(
            kind=kind,
            provider=msg.provider,
            subscription_key=msg.subscription_key,
            slot=msg.slot,
            err=err,
            latency=latency,
            raw=msg.payload,
        )

    raise UnsupportedNotification(msg.method)
