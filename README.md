# Snipe-Rugg

A real-time Solana wallet intelligence engine: track wallets and dev/creator activity
as it happens on-chain, detect buys/sells/launches with measured (not guessed) latency,
alert to Discord, and — separately, paper-trading only by default — evaluate follow
strategies against that activity.

The system is built around a stream, not a poll loop:

```
Solana RPC WebSocket ┐
Helius Enhanced WS   ┴─▶ Event Ingestion ─▶ Event Queue ─▶ Decoder ─▶ ... ─▶ Discord
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for build order and current status.

## Status: Phase 1 — Real-time core

What exists today: WebSocket connectivity to Solana (native RPC + optional Helius
Enhanced WS), automatic reconnect with resubscription, gap-recovery backfill over RPC
HTTP, provider-agnostic event normalization, signature dedup, an async event bus, and
per-stage latency telemetry. Wallet management, transaction decoding, buy/sell
detection, Discord alerts, dev tracking, strategy/paper trading, and the dashboard are
later phases — see the roadmap — and are not implemented yet.

## Quickstart

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[dev]" --python .venv/bin/python
cp .env.example .env   # fill in SOLANA_RPC_WS / HELIUS_API_KEY as desired

# Run the Phase 1 demo against real mainnet: connects, subscribes, logs every
# normalized event (with measured latency) to stdout as structured JSON.
.venv/bin/python -m snipe_rugg.main --wallet <SOME_WALLET_ADDRESS>

# Tests (no network required — WebSocket behavior is tested against a local
# in-process mock server, including real reconnect/resubscribe)
.venv/bin/pytest
```

Or via Docker Compose (also brings up Postgres and Redis for later phases):

```bash
docker compose up --build
```

## Safety posture

This project defaults to **observation and paper trading only**. There is no live
execution path in the codebase yet, and when one is added (last, per the roadmap) it
will ship disabled by default, gated behind explicit admin configuration, with hard
limits (max trade size, daily loss limit, position count, slippage, liquidity floor)
and manual confirmation before any real order. Private keys are never accepted through
Discord and are never stored in `.env`, the database, or logs.

## Project layout

```
src/snipe_rugg/
  config.py            # env-based settings
  logging_setup.py      # structured JSON logging
  core/
    events.py           # RawStreamMessage, NormalizedChainEvent, LatencyTrace
    normalize.py        # provider-specific payload -> NormalizedChainEvent
    event_bus.py         # async pub/sub
    dedup.py             # signature-based dedup (in-memory + Redis)
  providers/
    base.py              # BlockchainProvider / StreamingProvider / TransactionProvider
    solana_ws.py          # native Solana WS: subscribe + reconnect + resubscribe
    helius_ws.py           # Helius Enhanced WS (same protocol, faster infra)
    rpc_http.py            # JSON-RPC HTTP client (backfill, retries/backoff)
    manager.py              # multi-provider fan-out / redundancy
  ingestion/
    pipeline.py              # ingestion layer + event queue
    gap_recovery.py           # post-reconnect backfill
  main.py                     # Phase 1 demo entrypoint
tests/                        # pytest + pytest-asyncio, incl. a real mock WS server
docs/                          # architecture, roadmap, provider matrix
```
