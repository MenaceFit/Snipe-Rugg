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

## Status: Phase 3 — Token detector

Done so far: Phase 1 (WebSocket connectivity to Solana, reconnect/resubscribe,
gap-recovery backfill, event normalization, dedup, latency telemetry), Phase 2
(transaction decoding, buy/sell/transfer/mint/burn/... classification, a
SQLAlchemy-backed wallet/watchlist store, and a Discord bot with `/wallet` and
`/watchlist` slash commands that alert on tracked-wallet activity), and Phase 3
(Pump.fun / generic launch detection, auto-tracking a newly launched token
through to its graduation on PumpSwap, with "🚨 DEV LAUNCH DETECTED" and
"🎓 GRADUATED" alerts). Dev/graph/strategy tracking and paper trading are later
phases — see the roadmap — and aren't implemented yet.

## Quickstart

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[dev]" --python .venv/bin/python
cp .env.example .env   # fill in SOLANA_RPC_WS / HELIUS_API_KEY / DISCORD_TOKEN as desired

# Phase 1 demo: connects, subscribes, logs every normalized event (with
# measured latency) to stdout as structured JSON. No Discord/DB needed.
.venv/bin/python -m snipe_rugg.main --wallet <SOME_WALLET_ADDRESS>

# The real bot: Phase 1 streaming + Phase 2 tracking/decoding/alerting, live on
# Discord. Needs DISCORD_TOKEN and DISCORD_ALERT_CHANNEL_ID in .env.
.venv/bin/python -m snipe_rugg.bot_main

# Tests (no network required — WebSocket behavior is tested against a local
# in-process mock server including real reconnect/resubscribe; the DB layer
# runs against sqlite+aiosqlite; Discord command logic is tested as plain
# async methods, independent of a live gateway connection)
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
  decoder/
    transaction_decoder.py    # raw getTransaction(jsonParsed) -> DecodedTransaction
    classifier.py              # DecodedTransaction + wallet -> BUY/SELL/TRANSFER/...
    constants.py                # known program IDs/mints (labeling only)
  launchpad/
    detector.py                  # PumpFunLaunchDetector / GenericTokenCreationDetector
  db/
    base.py                     # async engine/session
    models.py                    # tracked_wallets, wallet_groups, token_trades, tokens, ...
    repository.py                 # the only thing that touches a session directly
  tracking/
    wallet_tracker.py             # event -> decode -> classify -> persist -> alert -> launch detect
    token_tracker.py               # watches launched mints for graduation to PumpSwap
  alerts/
    embeds.py                     # Discord embed builders (trade/activity/launch/graduation)
  discord_bot/
    services.py                   # /wallet and /watchlist command logic (no discord.py)
    bot.py                         # app_commands wiring
    alert_sink.py                  # the concrete AlertSink that posts to a channel
  main.py                         # Phase 1 demo entrypoint
  bot_main.py                      # full Phase 1-3 composition root
tests/                        # pytest + pytest-asyncio, incl. a real mock WS server
docs/                          # architecture, roadmap, provider matrix
```
