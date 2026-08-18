# Architecture

## Design principles

1. **Stream, never poll.** The core loop is WebSocket subscriptions
   (`logsSubscribe` / `accountSubscribe` / `programSubscribe` / `slotSubscribe` /
   `signatureSubscribe`) reacting to server-pushed notifications. There is no
   `while True: requests.get(...); sleep(1)` anywhere in this codebase, and there
   shouldn't be — a polling loop on the hot path is a rejected design, not an
   oversight.
2. **Measure latency, never claim it.** Every event carries a `LatencyTrace`
   (`snipe_rugg.core.events.LatencyTrace`) with a timestamp per pipeline stage.
   Each `*_latency_ms` property returns `None`, not a guess, when its inputs
   aren't both known yet. Nothing in this system prints a fixed "~150ms" figure —
   it prints what it actually measured for that event.
3. **Normalize early, decode later.** Solana native WS and Helius Enhanced WS
   speak the same JSON-RPC subscription protocol, so `core/normalize.py`
   converts either provider's raw notification into one `NormalizedChainEvent`
   shape. Turning that into a business-level event (BUY/SELL/TOKEN_CREATE/...) is
   the transaction decoder's job — a later phase — not the ingestion layer's.
4. **Redundancy over reactive failover.** `ProviderManager` doesn't wait to
   detect a primary provider's failure before switching — it subscribes on every
   configured provider simultaneously and lets the ingestion pipeline's
   signature-based dedup collapse duplicates. This trades some duplicate
   bandwidth for zero-detection-time failover, which matters more here than
   efficiency (see spec priorities: latency first).
5. **Paper trading and observation by default, live execution last and opt-in.**
   No execution path exists yet. When one is added (last, after the detection
   engine and paper trading are validated), it ships disabled by default, and
   private keys are never accepted through Discord, `.env`, the database, or
   logs — full stop, not a configuration option.

## Pipeline (target shape)

```
Solana RPC WebSocket ┐
Helius Enhanced WS   ┴─▶ Event Ingestion ─▶ Event Queue ─┬─▶ Transaction Decoder
                                                          ├─▶ Wallet State Engine
                                                          ├─▶ Token Discovery Engine
                                                          ├─▶ Dev Monitor
                                                          ├─▶ Strategy Engine
                                                          ├─▶ Alert Engine ─▶ Discord
                                                          └─▶ Database
```

**Phase 1** built the left-hand side of this diagram: both WebSocket sources,
the ingestion layer, and the event queue, ending at a provider-agnostic
`NormalizedChainEvent` published on an in-process event bus. **Phase 2** added
the Transaction Decoder, Alert Engine, and Database boxes — scoped to wallet
tracking, not the token/dev/strategy engines, which are still later phases —
see `ROADMAP.md`.

## Module map (implemented)

- `config.py` — `pydantic-settings`-based configuration, all fields optional
  except what the real-time core needs, so this layer runs standalone before
  the database or Discord bot exist.
- `logging_setup.py` — structured JSON logs (`extra={"fields": {...}}`).
- `core/events.py` — `RawStreamMessage` (raw notification + receipt metadata),
  `LatencyTrace`, `NormalizedChainEvent`, `SubscriptionKind`.
- `core/normalize.py` — raw notification → `NormalizedChainEvent`, per
  subscription kind.
- `core/event_bus.py` — async pub/sub. Topics are plain strings; Phase 1 only
  publishes `"chain.normalized_event"`. Later phases add topics as they need
  them (`new_trade`, `new_token`, `creator_action`, `strategy_signal`, ...) —
  they aren't pre-declared here since nothing produces them yet.
- `core/dedup.py` — signature-based dedup. `InMemoryDeduplicator` for a single
  process; `RedisDeduplicator` (Redis `SET NX EX`) for multi-process/multi-pod
  deployments.
- `providers/base.py` — `BlockchainProvider` (lifecycle), `StreamingProvider`
  (subscriptions), `TransactionProvider` (point lookups for backfill). Only
  these three: `MarketDataProvider`, `TokenProvider`, `ExecutionProvider` are
  real interfaces for later phases and will be added with their first
  implementation, not as empty shells now.
- `providers/solana_ws.py` — the subscription engine: connect, subscribe,
  reconnect with exponential backoff, resubscribe. See "Concurrency notes"
  below for two correctness details worth knowing before extending this file.
- `providers/helius_ws.py` — thin subclass pointed at Helius's endpoint; same
  protocol, different (lower-latency, LaserStream-backed) infrastructure.
- `providers/rpc_http.py` — JSON-RPC HTTP client with retry/backoff on
  429/5xx, used for backfill only, never the hot path.
- `providers/manager.py` — fans every subscribe call out to all configured
  providers; aggregates connection state (`CONNECTED` / `DEGRADED` /
  `DISCONNECTED`) across them.
- `ingestion/pipeline.py` — raw message → normalize → dedup → queue → publish.
- `ingestion/gap_recovery.py` — on reconnect, replays any signatures missed
  for tracked addresses via RPC HTTP `getSignaturesForAddress`, so a brief
  outage doesn't silently drop transactions.
- `main.py` — a real, runnable demo wiring the Phase 1 core together against
  actual Solana infrastructure (see README quickstart).
- `decoder/transaction_decoder.py` — raw `getTransaction(jsonParsed)` →
  `DecodedTransaction`, via balance deltas (`preBalances`/`postBalances`,
  `preTokenBalances`/`postTokenBalances`) rather than per-program instruction
  parsing — see "Why balance deltas" below.
- `decoder/classifier.py` — `DecodedTransaction` + a wallet → BUY/SELL/SWAP
  (`NormalizedTrade`) or TRANSFER/MINT/BURN/APPROVAL/STAKE/UNSTAKE/
  ACCOUNT_CREATE/ACCOUNT_CLOSE/TOKEN_CREATE (`NormalizedActivity`).
- `decoder/constants.py` — known program IDs/mints for DEX labeling only
  (never used to build or sign anything), and the infrastructure-program guard
  that keeps a self-mint from looking like a BUY.
- `db/` — async SQLAlchemy: `base.py` (engine/session), `models.py`
  (`tracked_wallets`, `wallet_groups`, `wallet_group_members`, `token_trades`,
  `wallet_activity`, `alerts`, `tokens`), `repository.py` (the only thing that
  touches a session directly).
- `launchpad/detector.py` — which platform (if any) a transaction created a
  new token on: `PumpFunLaunchDetector` (priority 1) then
  `GenericTokenCreationDetector`, composed by `detect_launch()`. See "Why
  balance deltas" below for why Pump.fun detection reads the SPL
  `initializeMint2` CPI instead of decoding Pump.fun's own instruction.
- `tracking/wallet_tracker.py` — consumes `"chain.normalized_event"`, fetches
  the full transaction for a tracked wallet's logs notification, decodes,
  classifies, persists, and alerts through an `AlertSink` protocol it doesn't
  know is backed by Discord. Also runs launch detection and, on a launch,
  auto-subscribes to the new mint (`token:{mint}` key) so `token_tracker.py`
  picks up its graduation later.
- `tracking/token_tracker.py` — watches auto-subscribed mints for graduation
  to PumpSwap; single-transaction detection (see "Why balance deltas" below),
  not a cross-transaction state machine.
- `launchpad/monitor.py` (`LaunchMonitor`) — network-wide Pump.fun launch
  discovery: one `logsSubscribe(mentions=[PUMP_FUN_PROGRAM])` subscription
  sees every launch on the platform, not just ones made by an
  already-tracked wallet. Auto-tracks unknown creators (`WalletSource.AUTO_DEV`)
  and subscribes to their wallet too, so their future activity keeps flowing
  through the normal pipeline.
- `alerts/sink.py` — the `AlertSink` Protocol (moved out of
  `tracking/wallet_tracker.py` in Phase 4 so `dev/alerts.py` could depend on
  it without importing `tracking/*` and risking a cycle; re-exported from
  `wallet_tracker.py` for the existing import path).
- `dev/profile.py`, `dev/patterns.py`, `dev/service.py`, `dev/alerts.py` — the
  dev monitor (spec section 20-37, 56-59, 84): `build_profile()` turns
  persisted `tokens`/`token_trades` rows into a `DevProfile` (pure function,
  no DB); `assess_dev()` turns a profile into a `DevRiskAssessment` (pattern
  thresholds, never a single-signal HIGH verdict — see "Rug-risk signal
  design" below); `DevMonitorService` persists/upserts the result;
  `dev/alerts.py`'s `refresh_and_maybe_alert()` is the one place
  `WalletTracker`/`TokenTracker`/`LaunchMonitor` all call to decide whether a
  profile change is alert-worthy.
- `alerts/embeds.py` — Discord embed builders (trade/activity/launch/
  graduation); no USD or token name/ticker fields until a market-data /
  metadata provider exists to back them. "Age" on the launch embed is real
  (computed from block_time vs now at build time), not the same kind of gap.
- `discord_bot/services.py` — `/wallet` and `/watchlist` command logic as
  plain async methods, independent of discord.py's `Interaction` machinery.
- `discord_bot/bot.py` / `discord_bot/alert_sink.py` — the `app_commands`
  wiring and the concrete `AlertSink` that actually posts to a channel; the
  only two files that import `discord` outside of `bot_main.py` itself.
- `bot_main.py` — the full composition root (Phases 1-3) that a real
  deployment runs.

## Why balance deltas, not per-program instruction parsing

The RPC's `jsonParsed` encoding auto-decodes System/Token/Token-2022/Stake
program instructions into a structured `{"type": ..., "info": {...}}` shape,
but Pump.fun/PumpSwap/Raydium/Jupiter instructions arrive as opaque
`programId` + base58 `data` — there's no generic parsed form for them the way
there is for the native programs, and decoding each DEX's specific layout
would mean pulling in and maintaining their IDLs. `preBalances`/`postBalances`
and `preTokenBalances`/`postTokenBalances` already say exactly what a
transaction did to a wallet's SOL and SPL holdings, regardless of which DEX
produced that result — so the classifier reasons about *what changed*, and
only reaches for parsed instructions (`decoder/classifier.py`) for the things
balance deltas can't tell you: mint/burn/approve/stake/create/close.

One real trap this caught during testing: minting tokens to yourself pays SOL
rent and receives tokens — the exact same balance-delta shape as a BUY. The
classifier refuses to call anything a trade unless a program outside a fixed
"wallet infrastructure" set (System/Token/Token-2022/AssociatedToken/Stake)
was actually invoked (`constants.has_external_program`); see
`test_self_mint_is_not_misclassified_as_a_buy` in the test suite.

The same "read what's already parsed instead of decoding opaque data" idea
carries into launch and graduation detection (`launchpad/detector.py`,
`tracking/token_tracker.py`): Pump.fun's `create` instruction is unparsed
custom Anchor data, but it always invokes SPL Token's `initializeMint2` as a
CPI to actually create the mint — and *that* instruction is parsed, so the
mint address comes from there. Graduation is detected the same
low-effort way: Pump.fun's `migrate` and PumpSwap's `create_pool` land in a
single transaction together, so "did this transaction touch both program
IDs" is sufficient — no need to correlate state across multiple transactions
over time. Neither mechanism required decoding a single byte of Pump.fun's
own instruction data, which is also exactly why token name/ticker aren't
available: those only exist inside that opaque data (or a Metaplex metadata
account this project doesn't fetch).

## Rug-risk signal design (spec section 62)

The one hard rule governing `dev/patterns.py`: never claim single-signal
proof of fraud. That's enforced structurally, not just through label wording.
Each of the four pattern detectors (`SERIAL_LAUNCHER`, `LOW_GRADUATION_RATE`,
`RAPID_RELAUNCH`, `DEV_SOLD_OWN_LAUNCH`) can independently report HIGH
severity for a creator address, but `assess_dev()`'s combination step only
lets the *overall* assessment reach HIGH when at least two of those signals
independently agree — a lone HIGH signal is still surfaced (callers can see
it in `DevRiskAssessment.signals`), but the combined read is capped at
MEDIUM. Alerting (`dev/alerts.py`) only fires on the combined read reaching
HIGH, so a single pattern alone never produces a "HIGH-RISK REPEATED
PATTERN" Discord alert — it takes corroboration. Labels are deliberately
"HIGH-RISK REPEATED PATTERN" / "REPEATED PATTERN — WATCH", never "rugger",
"scam", or "fraud" — this project observes and reports patterns, it doesn't
adjudicate intent.

## Concurrency notes (read before touching `solana_ws.py`)

Two bugs surfaced while writing the test suite for this file, both now fixed
and covered by tests, but worth understanding before modifying it:

1. **The read loop must run concurrently with subscribing, not before it.**
   A subscribe request's response is only ever resolved by `_handle_response`,
   which only runs inside `_read_loop`. The read loop is started as its own
   task *before* `_resubscribe_all()` sends anything — running them
   sequentially (resubscribe fully, then start reading) would leave every
   resubscribe call awaiting a response nothing is yet reading to deliver.
2. **The subscription-id mapping is registered inside `_handle_response`, not
   by the caller after `_send_request` returns.** The caller resumes in a
   different asyncio task than the one running the read loop; a notification
   for a subscription can be read (in the read-loop task) before the caller
   task is rescheduled to record the mapping. Registering it synchronously in
   `_handle_response`, at the moment the subscribe ack is processed, closes
   that window — by construction, not by timing luck.

## Known scaling limit (documented, not solved)

`logsSubscribe`'s `mentions` filter is only reliable with exactly one address
per subscription — `subscribe_logs()` enforces this and raises if you pass
more than one. Tracking many wallets means many logical subscriptions
multiplexed over a small number of physical connections (already how this
code works), not one subscription covering many addresses. That's fine up to
a provider's per-connection subscription cap (varies by provider and plan —
often low hundreds on a free/shared tier). Scaling cleanly into the
thousands-of-wallets range is a documented upgrade path, not something this
phase claims to solve: it means either connection-pooling with overflow
across multiple physical WS connections, or moving to a Geyser-compatible
gRPC stream (Helius LaserStream / Yellowstone) that supports server-side
filtering by many accounts in one stream. Neither is implemented — pulling in
a gRPC/protobuf dependency for Yellowstone is a real scope expansion that
belongs in its own phase, not bundled quietly into this one.

## Safety posture

- No execution path exists in this codebase yet.
- `/wallet import <private_key>` (or any Discord command accepting a private
  key) must never be implemented — this is a hard constraint, not a default.
- When a live execution adapter is eventually built (last, per the roadmap),
  it ships disabled by default, gated behind explicit admin configuration,
  with hard limits (max trade size, daily loss cap, max open positions,
  slippage ceiling, minimum liquidity) and manual confirmation before any
  real order.
