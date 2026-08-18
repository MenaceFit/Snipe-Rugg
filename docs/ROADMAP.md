# Roadmap

Build order is intentional and sequential — each phase is built and validated
before the next starts, not in parallel. This matches the mandatory order from
the original spec (section 151) and the success criteria in section 152.

## Phase 1 — Real-time core — **done**

RPC WebSocket connectivity, event ingestion, normalization.

- Native Solana WS provider: `logsSubscribe` / `accountSubscribe` /
  `programSubscribe` / `slotSubscribe` / `signatureSubscribe`, multiplexed over
  one connection, with reconnect (exponential backoff) and resubscription.
- Helius Enhanced WS provider (optional, same protocol, lower latency).
- `ProviderManager`: multi-provider redundancy/fan-out.
- RPC HTTP client with retry/backoff, used for gap-recovery backfill.
- `IngestionPipeline`: normalize → signature dedup → queue → publish.
- `GapRecoveryService`: replays missed signatures after a reconnect.
- Per-stage `LatencyTrace` on every event.
- 37 tests, including genuine reconnect/resubscribe behavior against a real
  local WebSocket server (not a mock of the provider's internals).
- Runnable demo: `python -m snipe_rugg.main --wallet <address>`.

Not yet validated against live mainnet from within a development session — see
the note in the top-level summary of whichever session built this; run the
quickstart yourself against a real endpoint to confirm.

## Phase 2 — Wallet tracker — not started

Spec sections 7–14, 20–25, 63, 71–77, 103–110.

- Wallet management (`/wallet add|remove|list|info|pause|resume`) and
  watchlists/groups (`/watchlist ...`).
- `TransactionDecoder`: raw transaction → `DecodedTransaction` (signature,
  slot, block_time, signer, programs, accounts, instructions, inner
  instructions, SOL/SPL transfers, swaps).
- `NormalizedTrade` / business-level `EventType` (BUY, SELL, TRANSFER, SWAP,
  TOKEN_CREATE, LIQUIDITY_ADD/REMOVE, MINT, BURN, ...) — these schemas belong
  next to the decoder that produces them, not defined speculatively in Phase 1.
- Buy/sell detection across SOL, wrapped SOL, USDC, multi-hop swaps.
- Price engine (execution price, USD/SOL value) from the transaction itself,
  not just a global market price.
- Discord alerts for the above, with the latency breakdown from spec section 4
  (detection / decode / Discord / total) shown per alert.
- PostgreSQL schema for `tracked_wallets`, `transactions`, `normalized_events`,
  `token_trades`, `token_transfers`.

## Phase 3 — Token detector — not started

Spec sections 16–19, 60, 149.

- `PumpFunLaunchDetector` (priority 1) behind a `LaunchpadDetector` interface
  so other launchpads can be added without rewriting the tracker.
- Creator/launch detection, graduation/migration/pool-creation detection.
- Token metadata provider integration (verify current docs/rate limits/pricing
  before implementing each one, per spec section 101 — the API matrix below is
  a starting point, not a substitute for checking at implementation time).

## Phase 4 — Dev monitor — not started

Spec sections 20–37, 56–59, 84.

- Dev/creator profiles, dev history, repeated-behavior / pattern-fingerprint
  detection.
- `DevCluster` / creator-cluster detection (common funding, timing, transfers).
- Rug-risk signal engine — multi-signal, probabilistic, explicitly never a
  single-signal verdict (spec section 62): outputs "HIGH-RISK REPEATED
  PATTERN", never an accusation of fraud.

## Phase 5 — Graph — not started

Spec sections 64–69, 122, 144–145.

- Wallet relationship graph (funded/transferred/created/bought/sold edges),
  bubble map, wallet/token detail pages, cross-wallet correlation.

## Phase 6 — Strategy — not started

Spec sections 38–39, 54–55, 88, 119–121, 126–140.

- `StrategyEngine`, decoupled from Discord.
- Paper trading only (`MODE = PAPER` by default and, at this phase, the only
  mode that exists at all).
- Exit-rule engine (`CreatorExitRule`), signal filters, risk gates.

## Phase 7 — Backtest — not started

Spec sections 41–47, 82–83, 91–94, 134–136.

- Historical replay engine (1x/10x/100x/1000x), latency/fee/slippage
  simulation, strategy comparison and parameter optimization.
- No look-ahead bias: a backtested decision only ever sees data that would
  have been available at that moment.

## Phase 8 — Optional execution adapter — not started, built last

Spec sections 50–53, 95–98.

- `ExecutionProvider` abstraction (`PaperExecutionProvider`,
  `ManualExecutionProvider`, `LiveExecutionProvider`).
- Built **only after** the detection engine and paper trading are validated —
  not alongside them.
- Live mode ships disabled by default; requires explicit admin configuration,
  hard limits (max trade, daily loss limit, max positions, slippage limit,
  liquidity minimum), and manual confirmation per trade.
- No private key ever accepted through Discord, `.env`, the database, or logs.
