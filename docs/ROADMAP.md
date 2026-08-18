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
  USD estimates and token name/ticker are deliberately absent (need a
  market-data / token-metadata provider — still not implemented, see Phase 3
  below); large-sell-% and dev-labeled alerts need position-size and
  creator-identity context that doesn't exist until the dev monitor (Phase 4).
- Full composition root: `python -m snipe_rugg.bot_main` (needs `DISCORD_TOKEN`
  and `DISCORD_ALERT_CHANNEL_ID`).

Not validated against a live Discord connection or live mainnet from within a
development session (same network-policy constraint as Phase 1, plus no bot
token in-session) — the command/decoder/classifier/tracker logic is
extensively tested locally, but running the bot for real needs to happen
outside this sandbox.

## Phase 3 — Token detector — **done**

Spec sections 16–18, 60.

- `LaunchpadDetector` interface with `PumpFunLaunchDetector` (priority 1, spec
  section 18) and a `GenericTokenCreationDetector` fallback. Pump.fun's `create`
  instruction is custom Anchor data this project doesn't decode (see
  `ARCHITECTURE.md` for why) — the mint address instead comes from the SPL
  `initializeMint2` instruction `create` always invokes as a CPI, which the RPC
  already parses. Creator is the transaction signer; Pump.fun's rare
  creator-≠-user free-mint flow isn't distinguished. No token name/symbol/ticker
  — those only exist in Pump.fun's opaque instruction data or a Metaplex
  metadata account this project doesn't fetch (a real, documented gap, not a
  silent one).
- `tokens` DB table (mint, creator, launchpad, pair, status, first/graduated
  slot+time+signature). `WalletTracker` now also runs launch detection after
  classifying a tracked wallet's transaction: it always persists a detected
  launch and auto-subscribes to the mint (regardless of alert preference —
  watching is a data-completeness concern, separate from notification), and
  sends the "🚨 DEV LAUNCH DETECTED" alert only when the wallet's
  `alert_launches` is on.
- `TokenTracker`: new tracker watching auto-subscribed mints for graduation
  (spec section 60). Detection is single-transaction, not stateful across
  many: a Pump.fun `migrate` and a PumpSwap `create_pool` land in the same
  transaction, so graduation is just "does this transaction touch both
  program IDs." Sends "🎓 GRADUATED" through the same `AlertSink` (extended
  with `send_launch`/`send_graduation` alongside the existing `send`), gated
  the same way (always persisted, alert only if the creator's
  `alert_launches` is on, or unconditionally if the creator wallet is no
  longer tracked).
- `bot_main.py` now subscribes both trackers to the event bus and, on
  startup, resubscribes not just active wallets but every ungraduated token
  too — so graduation tracking survives a restart the same way wallet
  tracking already did.
- Token metadata provider integration (name/ticker/USD, e.g. via Solscan /
  DEX Screener / Birdeye / on-chain Metaplex metadata) is explicitly **not**
  done — verify current docs/rate limits/pricing before implementing any of
  them, per spec section 101; `API_MATRIX.md` is a starting point, not a
  substitute for checking at implementation time.

Not validated against live mainnet from within a development session (same
constraint as Phases 1-2): the launch and graduation fixtures are realistic
but synthetic, built from verified program IDs and documented instruction
shapes rather than captured from an actual mainnet transaction.

## Phase 4 — Dev monitor — **done**

Spec sections 20–37, 56–59, 84.

- `dev/profile.py`: `build_profile()` — a pure function turning a creator's
  persisted `tokens` + `token_trades` rows into a `DevProfile` (launch count,
  graduated count/rate, average time between launches, average time to
  graduation, count of "sold its own launch within an hour" trades). Every
  field is a real aggregate; nothing here is estimated.
- `dev/patterns.py`: four pattern detectors over a `DevProfile` —
  `SERIAL_LAUNCHER` (N+ launches within a 24h window), `LOW_GRADUATION_RATE`
  (N+ launches, most/all still on the bonding curve), `RAPID_RELAUNCH`
  (launches averaging under 10 minutes apart), `DEV_SOLD_OWN_LAUNCH` (sold its
  own token within an hour of launching it, one or more times). Each threshold
  is an explicit, named constant. Spec section 62's "never a single-signal
  verdict" is enforced structurally, not just in wording: `assess_dev()`'s
  `_combine()` only reaches an overall HIGH once *two* independent signals are
  HIGH — a lone HIGH signal is still reported (in `signals`), but the combined
  read is capped at MEDIUM. Labels are "HIGH-RISK REPEATED PATTERN" /
  "REPEATED PATTERN — WATCH" — never "rugger", "scam", or "fraud".
- `dev/service.py` (`DevMonitorService`) + `dev/alerts.py`
  (`refresh_and_maybe_alert`): the shared recompute-persist-escalate path
  `WalletTracker`, `TokenTracker`, and the new `LaunchMonitor` all call after
  any activity that could change a creator's profile (a launch, a graduation,
  a sell). `dev_risk_signals` stores one upserted row per (creator, pattern) —
  re-detecting a standing pattern doesn't spam Discord again, and a pattern
  that later stops holding (e.g. a graduation improves the rate) is deleted,
  not left stale. Alerts fire only when the *combined* assessment reaches HIGH
  and something was newly escalated.
- `launchpad/monitor.py` (`LaunchMonitor`): closes a real gap Phase 3 left.
  `WalletTracker`'s launch detection only ever fired for a launch made *by an
  already-tracked wallet* — it can't discover a dev nobody added yet, which is
  the actual point of a launch monitor. `LaunchMonitor` subscribes once to
  `PUMP_FUN_PROGRAM`'s own logs (the same one-address-per-subscription
  `logsSubscribe` mechanism, just pointed at a program ID instead of a
  wallet) and sees every Pump.fun launch network-wide. On an unknown creator
  it auto-tracks them (`tracked_wallets.source = auto_dev`, conservative
  default alert flags — launches on, routine buy/sell/transfer alerts off)
  and subscribes to both the new mint and the creator's own wallet, so their
  *future* activity — further launches, a sell of what they just launched —
  keeps flowing through the same decode/classify/persist pipeline every other
  wallet uses. If `WalletTracker` already recorded the same launch first
  (the creator happened to already be tracked), `record_token_launch`'s
  existing idempotency makes this a harmless no-op, not a duplicate.
- `/dev profile <address>`: on-demand profile + risk-signal lookup,
  independent of whether any alert has ever fired for that address.
- `DevCluster` / cross-wallet creator-cluster detection (shared funding
  wallets, not just shared creator address) is **not** implemented here — it
  needs the wallet relationship graph, which is Phase 5's job; `early_sell`
  and the other single-address patterns above don't need it.

Not validated against live mainnet from within a development session (same
constraint as Phases 1-3): pattern thresholds are reasonable starting points,
not tuned against real rug-pull data, and are meant to be adjusted with real
observation, not treated as ground truth from day one.

## Phase 5 — Graph — **done**

Spec sections 64–69, 122, 144–145.

- `graph/builder.py` (`build_wallet_graph`): turns already-persisted rows into
  a typed `networkx.MultiDiGraph` — nothing new is tracked or inferred. SOL
  transfers (`WalletActivity` where `mint == "SOL"`) become `FUNDED` edges,
  SPL transfers become `TRANSFERRED` edges (direction from the sign of the
  recorded amount, sender → receiver), `TokenTrade` rows become `BOUGHT`/
  `SOLD` edges wallet → mint, and `Token.creator_address` becomes a `CREATED`
  edge creator → mint. Scoped to a starting set of addresses, not "the whole
  database" — a global graph isn't meaningful to render.
- `graph/analysis.py`: `funders_of` / `funded_by` / `shares_a_funder_with` /
  `funder_clusters` — cross-wallet correlation restricted to `FUNDED` edges.
  This is the concrete form of the "DevCluster" idea Phase 4 flagged as
  needing the graph: two token creators funded from the same wallet is a
  real, observable signal they may be the same operator running multiple
  accounts, without claiming that outright.
- `graph/service.py` (`GraphService`): builds one address's direct graph,
  then re-builds including its funders/funded addresses so `/wallet graph`
  shows a chain of funding relationships, not just one node's own edges.
  Bounded to one expansion pass, not open-ended recursion.
- `graph/render.py`: a real PNG bubble map (matplotlib, headless `Agg`
  backend — no display server needed), node size from real SOL-denominated
  volume through that node, edges colored/legended by kind. No fabricated
  "risk" visual encoding — size and color both come straight from the graph's
  own edge data.
- `/wallet graph <address>`: posts the bubble map plus a text summary
  (funders, who this address funded, other addresses sharing a funder).
- `networkx`/`matplotlib` moved from the `analysis` optional extra into core
  dependencies, matching how SQLAlchemy/discord.py were promoted once
  actually used (Phase 2) — `numpy`/`pandas` stay optional since nothing here
  uses them directly.

Not validated against live mainnet (same constraint as prior phases). Graph
construction and rendering are both exercised with real persisted rows in
tests; the bubble map's *layout* (networkx's spring layout) isn't visually
reviewed by a human in this sandbox — only that it produces a structurally
valid PNG.

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
