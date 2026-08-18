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
| DEX Screener | Market data (price, volume, liquidity) for tokens across DEXs | Not applicable (not streaming) | *Unverified — check current API tier/limits before use* | No (polling / webhook, not a live subscription) | N/A | Not implemented — needed for USD estimates, still missing from every alert embed |
| Birdeye | Market data, token metadata | Not applicable (not streaming) | *Unverified — check current API tier/limits before use* | Some WS support advertised; unverified | N/A | Not implemented |
| Metaplex Token Metadata (on-chain program, not an API) | Token name/symbol/uri, read directly from the mint's metadata PDA | RPC-dependent, not a separate service | Same as whichever RPC is already in use — no separate cost | No (point lookup via `getAccountInfo`) | N/A | Not implemented — the clear next step for token name/ticker over a paid market-data API, but its account layout wasn't verified carefully enough during Phase 3 to risk hand-decoding it; do that verification before implementing |
| Pump.fun (API) | Launch/graduation/bonding-curve data specific to Pump.fun | Not applicable | *Unverified — check current API terms before use* | Unverified | N/A | **Deliberately not used.** Launch detection (`launchpad/detector.py`) and graduation detection (`tracking/token_tracker.py`) were both built entirely from on-chain program IDs and the RPC's own instruction parsing — no Pump.fun API dependency at all, consistent with spec section 148 |

## Why native Solana WS + Helius, and nothing else, through Phase 3

The spec's own priority order (section 3: latency first; section 148: prefer
native chain data over market-data aggregators for "what did a wallet actually
do") points at exactly these two for the real-time core, and it held up
through token/launch detection too: Phase 3 needed zero market-data or
launchpad API integration, only program IDs and the RPC's own parsing.
Everything else in this table is either historical/indexed data (useful for
backfill and cross-checking, not for the hot path) or market-data (useful for
pricing, not for determining on-chain truth) — both are later-phase concerns
and are left unimplemented here rather than stubbed out speculatively.
