# Phase 0 Spike Notes — Polymarket CLOB V2 integration

Findings from 2026-07-03, verified against live docs and the installed SDK source
(not model memory). Later phases build on this; update it when facts change.

## 1. SDK decision: py-clob-client is dead, use `polymarket-client`

- **`py-clob-client` is archived (Feb 2026) and marked "no longer functional" by
  Polymarket.** It has no builder-code support. The CLAUDE.md stack line predates this.
- The official successor is the unified Python SDK: **`polymarket-client`** on PyPI
  (repo: Polymarket/py-sdk), currently beta (`0.1.0b13` installed, requires
  `uv add --prerelease allow polymarket-client`). It covers public data (Gamma),
  authenticated CLOB trading, builder attribution, and wallet/relayer workflows in
  one package — no Node signing sidecar needed.
- Docs also reference a `py_clob_client_v2` package in some examples; the archive
  banner on py-clob-client points at py-sdk, which is what we use.

## 2. V2 order struct and builder attribution

- The EIP-712 order struct (domain "Polymarket CTF Exchange" version "2") is:
  `salt, maker, signer, tokenId, makerAmount, takerAmount, side, signatureType,
  timestamp, metadata (bytes32), builder (bytes32)`.
  V1's `expiration/nonce/feeRateBps` are gone; **`builder` is a first-class signed
  field**, so attribution is tamper-proof once signed.
- Attribution = pass `builder_code=` to order methods. No HMAC builder headers, no
  builder API key (V2 removed that flow). The code is a public bytes32.
- Builder fee rates are configured on the builder profile
  (polymarket.com/settings?tab=builder): `builder_taker_fee_bps` (cap 100 = 1%) and
  `builder_maker_fee_bps` (cap 50). Changeable once/week with 3-day notice.
  **Our 1% launch fee is exactly the taker cap.**
- `client.get_builder_fee_rates(code)` returns the live rates; volume/fee credit is
  queryable via `list_builder_trades` / builder leaderboard endpoints.

## 3. Auth model (V2)

- `SecureClient.create(private_key=..., wallet=...)` does everything: derives L2 API
  credentials from an L1 EIP-712 signature, classifies the wallet type, and resolves
  the funder wallet (defaults to the signer's "Deposit Wallet").
- Signature type 3 (`POLY_1271`, "DepositWallet" domain) is handled automatically —
  relevant because Polymarket web accounts and our future per-user wallets are smart
  contract wallets, not EOAs.
- The optional `api_key=` parameter on `create()` is for **gasless wallet and relayed
  transaction workflows** (the Relayer API keys mentioned in the docs) — this is the
  credential we'll need for Phase 1 wallet infra, not for trading or attribution.

## 4. Market orders and slippage guard (F-10)

- `client.place_market_order(token_id=, side=, amount=|shares=, order_type="FOK",
  builder_code=, max_price=|min_price=, max_spend=)` creates, signs, and posts in one
  call. Default order type is **FAK** — we must pass `"FOK"` explicitly per PRD.
- **The slippage guard is native**: `max_price` (BUY) / `min_price` (SELL) become the
  order's limit price instead of an estimated book price. Protected prices must
  conform to the market's tick size.
- BUY sizes by USDC `amount`; SELL sizes by `shares`. `max_spend` on BUY makes the SDK
  shrink the order so amount + platform fee + builder fee never exceeds the cap —
  note **Polymarket now has a platform fee** (rate × (p(1-p))^exponent, fetched per
  market) on top of builder fees; confirmation screens must show both (F-13, D-10).
- Responses normalize to `AcceptedOrder` (order_id, status live/matched/delayed,
  making_amount, taking_amount, trade_ids, transactions_hashes) or `RejectedOrder`
  (typed codes incl. `fok_not_filled`, `not_enough_balance`). BUY: making=USDC,
  taking=shares. SELL: making=shares, taking=USDC.
- The SDK auto-recovers missing token allowances when posting
  (`post_order_with_allowance_recovery`), so no manual approval txs.

## 5. Market data (F-8, F-9)

- `PublicClient().get_market(url=...)` accepts a raw Polymarket URL directly — no
  slug parsing needed on our side. Also `slug=` and `id=` variants, plus
  `list_markets`, `search`, `list_events` for /trending and /search.
- The `Market` model is **binary-only by design** and raises
  `UnexpectedResponseError` for multi-outcome events — aligns exactly with D-4;
  catch it and reply "binary markets only."
- `market.outcomes.yes/.no` each carry `label`, `token_id`, `price`;
  `market.state` has `active/closed/end_date`; `condition_id` links to CLOB.
- Live pricing: `get_midpoint`, `get_price(side=)`, `get_order_book`,
  `estimate_market_price` (order-book-aware fill estimate — use this for previews).

## 6. Deposit detection (F-3, D-8)

- Plain JSON-RPC `eth_getLogs` with the ERC-20 Transfer topic filtered by recipient
  works fine; no indexer needed at our scale. Poll ~30s, credit at 12 confirmations.
- Watch BOTH tokens: **USDC.e `0x2791...4174` (bridged — Polymarket's collateral)**
  and native USDC `0x3c49...3359`. Users depositing native USDC will need a swap
  step before trading — decide handling in Phase 1 (warn vs auto-swap).
- **Keyless public RPC reality check (tested 2026-07-03):** polygon-rpc.com now
  requires an API key (401); publicnode.com serves `eth_blockNumber` but 403s
  `eth_getLogs`; **1rpc.io/matic works but caps `eth_getLogs` at 50 blocks/call**
  (the watcher chunks requests accordingly). Also: 1rpc.io rejects a trailing slash
  on the path. Verified live: ~11k USDC transfers per 50 blocks network-wide, filter
  by recipient topic works, PENDING→CONFIRMED flow observed on real transfers.
  Prod needs a keyed RPC provider (e.g. Alchemy/Infura free tier) — 50-block chunks
  at scale would be miserable.

## 7. Spike scripts (scripts/spike/)

| Script | Purpose | Credentials |
|---|---|---|
| `fetch_market.py <url>` | Market card data: question, token IDs, prices | none |
| `place_trade.py <url> --amount 1` | FOK buy with builder code + slippage guard, then sell back | `POLYMARKET_PRIVATE_KEY` (+ optional `POLYMARKET_WALLET`), `FADE_BUILDER_CODE` |
| `watch_deposits.py <address>` | Poll Polygon for USDC transfers, PENDING→CONFIRMED | none (public RPC) |

`place_trade.py` refuses amounts > $5, prompts for confirmation, prints fills with
polygonscan links, and never prints key material.

## 8. Open items for Phase 1

- **Per-user wallets:** the SDK has relayer models and gasless workflows via
  `api_key=`; the exact Safe-deployment flow for creating OUR users' wallets still
  needs a spike against the Relayer API (needs a Relayer API key from the builder
  dashboard).
- Native-USDC deposits need a swap/convert decision.
- Verify builder attribution actually credits after the first real trade (task 3 run).
