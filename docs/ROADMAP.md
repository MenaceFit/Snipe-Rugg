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

## Phase 2 — Wallet tracker — **done**

Spec sections 7–15, 23–30, 63, 70 (subset).

- `TransactionDecoder`: raw `getTransaction(jsonParsed)` → `DecodedTransaction`
  (signature, slot, block_time, success, signer, programs, accounts,
  instructions, inner instructions, SOL/SPL balance deltas), balance-delta
  based rather than per-program instruction parsing — see
  `ARCHITECTURE.md` for why.
- Classifier: `DecodedTransaction` + a wallet → BUY/SELL/SWAP
  (`NormalizedTrade`) or TRANSFER/MINT/BURN/APPROVAL/STAKE/UNSTAKE/
  ACCOUNT_CREATE/ACCOUNT_CLOSE/TOKEN_CREATE (`NormalizedActivity`). Requires an
  external (non-System/Token/Stake) program before calling something a trade,
  specifically so minting tokens to yourself doesn't look like a BUY.
  LIQUIDITY_ADD/LIQUIDITY_REMOVE are **not** implemented — no generic parsed
  form exists for them the way there is for System/Token/Stake; that needs
  each DEX's own instruction layout and is Phase 3 work alongside graduation
  detection.
- SQLAlchemy async DB layer: `tracked_wallets`, `wallet_groups`,
  `wallet_group_members`, `token_trades`, `wallet_activity`, `alerts`. Tests
  run against sqlite+aiosqlite; production targets postgres+asyncpg per
  `.env.example`. No migrations yet (Alembic) — `init_models()` is
  create-all convenience, not a substitute once there's data worth keeping.
- `WalletTracker`: consumes Phase 1's normalized event stream, fetches the
  full transaction, decodes, classifies, persists, and alerts — decoupled from
  discord.py via an `AlertSink` protocol, so it's fully unit-tested without a
  live Discord connection.
- Discord bot: `/wallet add|remove|list|info|pause|resume` and
  `/watchlist add|add-wallet|list` slash commands. Adding a wallet subscribes
  it live immediately; pausing unsubscribes, resuming resubscribes. Command
  logic lives in a plain-Python service layer (`discord_bot/services.py`),
  tested directly; `discord_bot/bot.py` is just the `app_commands` wiring on
  top of it.
- Alert embeds with the real measured latency breakdown from spec section 4.
  USD estimates and token age are deliberately absent (need a market-data /
  token-metadata provider — Phase 3); large-sell-% and dev-labeled alerts need
  position-size and creator-identity context that doesn't exist until the dev
  monitor (Phase 4).
- Full composition root: `python -m snipe_rugg.bot_main` (needs `DISCORD_TOKEN`
  and `DISCORD_ALERT_CHANNEL_ID`).

Not validated against a live Discord connection or live mainnet from within a
development session (same network-policy constraint as Phase 1, plus no bot
token in-session) — the command/decoder/classifier/tracker logic is
extensively tested locally, but running the bot for real needs to happen
outside this sandbox.

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
