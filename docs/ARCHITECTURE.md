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

**What exists today (Phase 1)** is the left-hand side of this diagram: both
WebSocket sources, the ingestion layer, and the event queue, ending at a
provider-agnostic `NormalizedChainEvent` published on an in-process event bus.
Everything right of "Event Queue" is a later phase and does not exist yet — see
`ROADMAP.md`.

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
- `main.py` — a real, runnable demo wiring all of the above together against
  actual Solana infrastructure (see README quickstart).

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
