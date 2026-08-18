"""Helius Enhanced WebSockets: the same Solana JSON-RPC subscription protocol as
solana_ws.py, served from Helius's LaserStream-backed infrastructure for lower and
more consistent latency (Helius documents Enhanced WebSockets as ~1.5-2x faster than
standard Agave RPC WebSockets). See docs/API_MATRIX.md, and re-verify against
https://www.helius.dev/docs before relying on specific latency/pricing numbers —
provider terms change over time.
"""
from __future__ import annotations

from snipe_rugg.providers.solana_ws import SolanaWebSocketProvider

DEFAULT_HELIUS_WS_ENDPOINT = "wss://mainnet.helius-rpc.com"


class HeliusWebSocketProvider(SolanaWebSocketProvider):
    def __init__(
        self, api_key: str, *, endpoint: str = DEFAULT_HELIUS_WS_ENDPOINT, name: str = "helius_ws", **kwargs: object
    ) -> None:
        if not api_key:
            raise ValueError("Helius API key is required")
        url = f"{endpoint}/?api-key={api_key}"
        super().__init__(url, name=name, **kwargs)  # type: ignore[arg-type]
