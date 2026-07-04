# Phase 1 Report — PRD F-1 through F-5, F-7 through F-13

Date: 2026-07-04. Scope per PRD §5 (P0 solo core). Suite: 65 tests passing,
ruff clean, no live network calls in tests, key material isolated to the
wallets signing store.

| F# | Requirement | Status |
|----|-------------|--------|
| F-1 | `/start` creates account after attestation gate (18+, jurisdiction, ToS) | **Implemented.** Gate blocks all trading until accepted (`test_attestation_gate_blocks_trading_until_accepted`). Wallet auto-deploys via Builder API key through the gasless Relayer; failure mid-flow gets a retry path. Attestation version + timestamp stored per user. |
| F-2 | `/wallet` shows address, USDC balance, open-position value | **Implemented with one gap.** Address + live pUSD balance + per-network deposit addresses shown. *Open-position value is NOT included on the wallet screen* — it lives on /positions instead; merging it in is a fast follow (needs one extra data-API call). QR code not rendered (address is tap-to-copy monospace text). |
| F-3 | Deposit detection + DM confirmation | **Implemented.** 30s background scan of pUSD/USDC/USDC.e transfers into all user wallets, chunked `eth_getLogs`, 12 confirmations, restart-safe watermark, unique tx hashes (no double-crediting; tested). |
| F-4 | `/withdraw` with first-time-address confirm; always available | **Implemented.** Amount → address → explicit confirm (distinct first-time warning; tested), bridge withdrawal to USDC on Polygon, receipt with tx hash. Ignores the trading pause flag by construction (tested). |
| F-5 | `/export` key export after warning flow | **Implemented.** Warning screen first, key only after explicit acknowledgement, DM-only at both steps (tested). |
| F-7 | Wallet actions DM-only; group invocations redirect to DM | **Implemented.** /start, /wallet, /positions, /withdraw, /export all redirect from groups with a deep-link button; group replies contain no addresses/balances (tested per handler). |
| F-8 | Paste URL → market card with Buy YES/NO and $10/$25/$50/custom | **Implemented.** Binary-only (SDK enforces; polite rejection otherwise), sanitized titles, size picker + confirm step, custom amount flow with $1–$1000 bounds (tested). |
| F-9 | `/trending` and `/search` | **Implemented, minimal.** Top-volume open binary markets / Gamma search; results are tappable buttons that swap in the tradeable card. *Category browsing from the PRD is deferred* — needs tag-based navigation UI, low value at current user count. |
| F-10 | FOK market orders with worst-price slippage guard | **Implemented.** Always FOK, guard = estimate ± user slippage (default 2%, stored per user), BUY `max_price` / SELL `min_price` (tested, incl. exact guard values). |
| F-11 | Builder code attached to every order | **Implemented.** From `FADE_BUILDER_CODE` env var on both BUY and SELL (tested). |
| F-12 | `/positions` with live PnL and one-tap Sell | **Implemented.** Data-API positions with cash/percent PnL, per-position Sell button → confirm → FOK sell of full position (tested). Partial sells deferred (v1 sells full position, matching one-tap intent). |
| F-13 | Confirmations show fill price, size, fee, new balance | **Implemented with an honest fee caveat.** Shows avg fill price, shares, builder fee (live rate × fill), and new pUSD balance. *Polymarket's platform fee (rate×(p(1-p))^exp, discovered in the spike) is charged by Polymarket but not itemized in our confirmation* — the new-balance line reflects it implicitly. Itemizing it is a fast follow in `docs/SPIKE-NOTES.md` §4. |

## Out-of-scope items pulled forward

- **F-25 kill switch (P0, §5.4):** implemented early because the Order path
  needed the check anyway — admin-gated /pause & /resume (numeric ID from
  `FADE_ADMIN_USER_ID`), DB-backed flag, blocks orders before the CLOB is
  touched, never blocks withdrawals (all tested).

## Architecture compliance (CLAUDE.md)

- Signing isolation: `fade.wallets.signing` is the only module touching key
  material; separate Fernet-encrypted SQLite store (`SIGNING_DATABASE_URL`),
  never the app DB (tested: key absent from main DB bytes; encrypted at rest).
- Idempotency: orders insert a unique `telegram_update_id` row BEFORE the CLOB
  call; a duplicated update returns `duplicate` and the CLOB sees exactly one
  order (`test_duplicate_update_id_does_not_create_second_order`).
- Sanitization: market titles/usernames stripped of Markdown/command syntax
  before rendering (tested).
- SDK: `polymarket-client` (py-sdk). py-clob-client is archived/dead — see
  `docs/SPIKE-NOTES.md` §1; nothing needed stubbing, the successor SDK covers
  all Phase 1 operations natively (verified against its source).

## Known limitations (recorded, not faked)

1. Live BUY orders from Jayden's location are geoblocked by Polymarket
   (close-only region). Order path is fully tested against a mock CLOB client;
   end-to-end fill needs a non-restricted region.
2. Market/position button state is in-memory; a bot restart expires open
   cards (users are told to re-paste). Fine for v1 polling mode.
3. Prod webhook mode (FastAPI receiver) not built yet — dev polling only.
   Idempotency protection is already webhook-retry-shaped.
4. `/wallet` omits open-position value; platform fee not itemized (see F-2,
   F-13 rows above).
