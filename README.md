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

## Status: all 8 phases complete

- **Phase 1** — WebSocket connectivity to Solana (native + optional Helius),
  reconnect/resubscribe, gap-recovery backfill, event normalization, dedup,
  per-stage latency telemetry.
- **Phase 2** — transaction decoding via balance deltas,
  buy/sell/transfer/mint/burn/... classification, a SQLAlchemy-backed
  wallet/watchlist store, and a Discord bot (`/wallet`, `/watchlist`) that
  alerts on tracked-wallet activity with real measured latency.
- **Phase 3** — Pump.fun / generic launch detection, auto-tracking a newly
  launched token through to its graduation on PumpSwap ("🚨 DEV LAUNCH
  DETECTED" / "🎓 GRADUATED").
- **Phase 4** — network-wide Pump.fun launch discovery (`LaunchMonitor`),
  auto-tracking unknown creators, dev/creator profiles built from real
  launch/trade history, four repeated-behavior pattern detectors, and
  "🚩 HIGH/MEDIUM-RISK PATTERN DETECTED" alerts that require corroborating
  signals before reaching HIGH (`docs/ARCHITECTURE.md`: "Rug-risk signal
  design").
- **Phase 5** — wallet relationship graph (funded/transferred/created/
  bought/sold edges from already-persisted rows), cross-wallet funder
  correlation, `/wallet graph` posting a rendered PNG bubble map.
- **Phase 6** — a paper strategy engine that follows a tracked wallet's own
  real buys and exits on `CreatorExitRule` (the token's own creator
  selling), reporting through `/strategy status|positions|pnl`
  (`docs/ARCHITECTURE.md`: "Why entries don't snipe at launch").
- **Phase 7** — a backtest engine (`/backtest run`) that replays this
  deployment's own recorded history through the exact live strategy engine
  in an isolated scratch database, with look-ahead structurally impossible
  rather than just avoided (`docs/ARCHITECTURE.md`: "No look-ahead"), plus
  win rate/PnL/drawdown metrics and strategy comparison.
- **Phase 8** — the `ExecutionProvider` abstraction (Paper/Manual/Live).
  `bot_main.py` only ever constructs `PaperExecutionProvider`; live
  execution is disabled by default, holds no signing material (an
  externally-injected `Signer` this repo ships zero implementations of),
  and enforces hard limits before every submission
  (`docs/ARCHITECTURE.md`: "Safety posture").

229 tests, ruff clean, mypy clean. Not validated against live mainnet or a
live Discord connection from within a development sandbox — see each
phase's section in `docs/ROADMAP.md` for exactly what that does and doesn't
mean for what's been tested.

## Quickstart

```bash
uv venv --python 3.12 .venv
uv pip install -e ".[dev]" --python .venv/bin/python
cp .env.example .env   # fill in SOLANA_RPC_WS / HELIUS_API_KEY / DISCORD_TOKEN as desired

# Phase 1 demo: connects, subscribes, logs every normalized event (with
# measured latency) to stdout as structured JSON. No Discord/DB needed.
.venv/bin/python -m snipe_rugg.main --wallet <SOME_WALLET_ADDRESS>

# The real bot: all 8 phases (streaming, tracking, dev monitor, graph,
# strategy, backtest, execution) wired together, live on Discord. Needs
# DISCORD_TOKEN and DISCORD_ALERT_CHANNEL_ID in .env.
.venv/bin/python -m snipe_rugg.bot_main

# Tests (no network required — WebSocket behavior is tested against a local
# in-process mock server including real reconnect/resubscribe; the DB layer
# runs against sqlite+aiosqlite; Discord command logic is tested as plain
# async methods, independent of a live gateway connection)
.venv/bin/pytest
```

Or via Docker Compose (also brings up Postgres; a Redis container is included
for `core/dedup.py`'s `RedisDeduplicator`, a multi-process alternative to the
in-memory one `bot_main.py` actually uses today — not yet wired in, since
this deployment is still single-process):

```bash
docker compose up --build
```

## Safety posture

This project defaults to **observation and paper trading only**. `bot_main.py` never
constructs anything but `PaperExecutionProvider` — there is no way to reach live
execution by running this bot as it exists in this repository. `execution/live.py`'s
`LiveExecutionProvider` ships disabled by default, holds no signing material (it
depends on an externally-injected `Signer` this repo ships zero implementations of —
see its own docstring for why that's permanent), and enforces hard limits (max trade
size, trailing-24h realized-loss cap, max open positions) before every submission.
`execution/manual.py` adds an explicit confirmation gate on top of any provider for
manual-approval workflows. Private keys are never accepted through Discord and are
never stored in `.env`, the database, or logs — not a policy, a structural fact about
what code exists in this repository.

## Project layout

```
src/snipe_rugg/
  config.py            # env-based settings
  logging_setup.py      # structured JSON logging
  core/
    events.py           # RawStreamMessage, NormalizedChainEvent, LatencyTrace
    normalize.py        # provider-specific payload -> NormalizedChainEvent
    event_bus.py         # async pub/sub
    topics.py             # EventBus topic names shared across modules
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
    monitor.py                    # LaunchMonitor: network-wide Pump.fun launch firehose
  dev/
    profile.py                   # build_profile(): tokens/trades rows -> DevProfile (pure)
    patterns.py                   # assess_dev(): DevProfile -> DevRiskAssessment
    service.py                    # DevMonitorService: DB-backed refresh/persist
    alerts.py                     # refresh_and_maybe_alert(): shared escalation gate
  graph/
    builder.py                   # build_wallet_graph(): persisted rows -> networkx MultiDiGraph
    analysis.py                   # funders_of / shares_a_funder_with / funder_clusters
    service.py                    # GraphService: DB-backed one-hop funder expansion
    render.py                     # render_bubble_map(): graph -> PNG bytes
  strategy/
    rules.py                     # should_enter(): dev-risk gate over a DevRiskAssessment
    engine.py                     # StrategyEngine: TOPIC_NEW_TRADE -> paper entries/exits
  backtest/
    replay.py                    # ReplayEngine: chronological, isolated-DB replay
    source.py                     # load_events(): live DB history -> ReplayEvent feed
    metrics.py                    # win rate / PnL / drawdown / hold time
    service.py                    # BacktestService: scratch-DB provisioning, run()/compare()
  execution/
    base.py                      # ExecutionProvider Protocol
    paper.py                      # PaperExecutionProvider - the only one bot_main.py builds
    manual.py                     # ManualExecutionProvider - confirmation gate, no UI wired
    live.py                       # LiveExecutionProvider - disabled by default, no key ever
  db/
    base.py                     # async engine/session
    types.py                     # ExactNumeric: exact Decimal storage on SQLite too
    models.py                    # tracked_wallets, wallet_groups, token_trades, tokens, paper_positions, ...
    repository.py                 # the only thing that touches a session directly
  tracking/
    wallet_tracker.py             # event -> decode -> classify -> persist -> alert -> launch detect
    token_tracker.py               # watches launched mints for graduation to PumpSwap
  alerts/
    sink.py                       # the AlertSink Protocol (business logic <-> discord.py seam)
    embeds.py                     # Discord embed builders (trade/activity/launch/graduation/dev-risk)
  discord_bot/
    services.py                   # /wallet, /watchlist, /dev, /wallet graph, /strategy, /backtest logic (no discord.py)
    bot.py                         # app_commands wiring
    alert_sink.py                  # the concrete AlertSink that posts to a channel
  main.py                         # Phase 1 demo entrypoint
  bot_main.py                      # full Phase 1-8 composition root
tests/                        # pytest + pytest-asyncio, incl. a real mock WS server
docs/                          # architecture, roadmap, provider matrix
```
