# Provider matrix

Per spec section 101: verify current docs for a provider immediately before
implementing it — figures below (especially pricing, rate limits, and
anything from a vendor blog post) drift, and the ones marked *unverified*
were never confirmed against primary docs in the first place. Treat this file
as a starting point for that check, not a substitute for it.

| Provider | Use case | Latency | Rate limit / cost | Streaming support | Fallback | Status |
|---|---|---|---|---|---|---|
| Solana public RPC (`api.mainnet-beta.solana.com`) | Default HTTP + WS endpoint, backfill, dev/testing | Baseline (no SLA) | Public, shared, rate-limited — not meant for production load; *exact current limits unverified, check before relying on this in production* | Yes — native `logsSubscribe` / `accountSubscribe` / `programSubscribe` / `slotSubscribe` / `signatureSubscribe` | N/A (this *is* the fallback tier) | Implemented (`providers/solana_ws.py`, `providers/rpc_http.py`) |
| Helius (Enhanced WebSockets) | Low-latency primary streaming provider | Vendor states ~1.5–2x faster than standard Agave WS, "up to 200ms faster" (Helius blog, 2026 — *unverified independently, re-check at implementation time*) | Requires paid API key; credit-based pricing that changes over time — check current plan pricing before budgeting | Yes — same JSON-RPC subscription protocol as native Solana WS, backed by Helius's LaserStream infrastructure | Falls back to / races against native Solana WS via `ProviderManager` | Implemented (`providers/helius_ws.py`) — thin subclass of the native WS provider |
| Helius LaserStream (gRPC, Yellowstone-compatible) | Higher-throughput account/tx/block streaming with server-side filtering by many accounts at once, historical replay | Vendor-stated lowest-latency tier | Separate gRPC connection quota from Enhanced WebSockets; check current plan | Yes — gRPC, not JSON-RPC WS | N/A | **Not implemented.** Documented upgrade path for the "1000s of wallets" scaling case (see `ARCHITECTURE.md`); pulling in `grpc`/protobuf bindings for the Yellowstone geyser proto is a real dependency addition, not something to bundle into Phase 1 |
| Solscan | Human-readable explorer links in Discord embeds | Not applicable (not streaming) | N/A — only the public `solscan.io/tx/<sig>` URL pattern is used, no API/key | No | N/A | Partially implemented: `alerts/embeds.py` links out to it; no API integration (no cross-checking, no data pulled from it) |
| DEX Screener | Trending-token *discovery* only (`discovery/dexscreener.py`, Phase 9) — which Solana tokens to look at, not a source of price/trade data | Not applicable (not streaming) | Public, no API key; `/latest/dex/search` and `/latest/dex/tokens/{addresses}` both documented at 300 req/min (secondary sources — see below) | No (polling, not a live subscription); no dedicated "trending" endpoint either — `discovery/trending.py` derives one by searching broadly and sorting by 24h volume | N/A | **Implemented for discovery only** (`discovery/dexscreener.py`, `discovery/trending.py`). *Schema unverified against a live response* — both `api.dexscreener.com` and `docs.dexscreener.com` are unreachable from this project's development sandbox (confirmed via direct `curl`); field names were cross-referenced from two independent secondary sources instead (see the client's own docstring). Still not used for USD estimates or any price data anywhere — every price in this codebase, including Phase 9's top-trader stats, comes from a real decoded on-chain trade, never from this API |
| Birdeye | Market data, token metadata | Not applicable (not streaming) | *Unverified — check current API tier/limits before use* | Some WS support advertised; unverified | N/A | Not implemented |
| Metaplex Token Metadata (on-chain program, not an API) | Token name/symbol/uri, read directly from the mint's metadata PDA | RPC-dependent, not a separate service | Same as whichever RPC is already in use — no separate cost | No (point lookup via `getAccountInfo`) | N/A | Not implemented — the clear next step for token name/ticker over a paid market-data API, but its account layout wasn't verified carefully enough during Phase 3 to risk hand-decoding it; do that verification before implementing |
| Pump.fun (API) | Launch/graduation/bonding-curve data specific to Pump.fun | Not applicable | *Unverified — check current API terms before use* | Unverified | N/A | **Deliberately not used.** Launch detection (`launchpad/detector.py`) and graduation detection (`tracking/token_tracker.py`) were both built entirely from on-chain program IDs and the RPC's own instruction parsing — no Pump.fun API dependency at all, consistent with spec section 148 |

## Why native Solana WS + Helius, and nothing else, through all 8 phases

The spec's own priority order (section 3: latency first; section 148: prefer
native chain data over market-data aggregators for "what did a wallet actually
do") points at exactly these two for the real-time core, and it held up
through every later phase, not just token/launch detection: the dev monitor
(Phase 4), graph (Phase 5), strategy engine and backtest (Phases 6-7) all
price and reason about activity using only real on-chain trade rates already
being decoded for the hot path — none of them needed a market-data API
either. That's a real, load-bearing consequence, not a coincidence: it's
exactly why `strategy/engine.py` doesn't snipe a token the instant its launch
is detected (see `ARCHITECTURE.md`'s "Why entries don't snipe at launch") —
an instant entry would have been the first place in the whole project that
actually needed a price from somewhere other than a real trade. Everything
else in this table is either historical/indexed data (useful for backfill
and cross-checking, not for the hot path) or market-data (useful for USD
pricing, not for determining on-chain truth) — both remain unimplemented
here rather than stubbed out speculatively; USD-denominated alerts and a
live bonding-curve price read are the two concrete places a market-data
provider would plug in next, and both are documented gaps, not silent ones.

## Why Phase 9 uses DexScreener without breaking the rule above

Phase 9's top-trader/insider feature is the first place this project calls a
market-data aggregator at all, but it doesn't actually cross the line the
sections above are protecting: DexScreener is called exactly once per
`/token trending` invocation, purely to answer "which Solana tokens are
trending right now" — a question with no on-chain equivalent (there's no
program you can subscribe to for "trending"). Every number `/token traders`
subsequently reports (buy/sell price, PnL, win rate) still comes from real
decoded on-chain trades via this project's existing Solana RPC/decoder/
classifier stack, exactly like every other phase — DexScreener never
supplies a price, a balance, or a trade in this codebase. If a genuine
multi-chain expansion is ever built, it would need each chain's own
decoder/RPC stack the same way Solana has one today; DexScreener's own
transaction/trade data was deliberately not used as a shortcut around that,
consistent with spec section 148's preference for native chain data.
