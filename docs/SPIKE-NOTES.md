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

## 8. Deposit-wallet deployment requires a Relayer API key (confirmed 2026-07-03)

- Running `place_trade.py` with a brand-new signer (no prior Polymarket account)
  failed: `SecureClient.create()` computes a deterministic deposit-wallet address
  (`derive_beacon_deposit_wallet_address(signer.address, ...)`), sees it isn't
  deployed on-chain, and tries to auto-deploy it via the gasless Relayer — which
  raised `Gasless transactions require a Builder API Key or Relayer API Key.`
- This is the crux of Phase 1's wallet flow: **Fade users won't have existing
  Polymarket accounts**, so every `/start` needs the backend to auto-deploy a fresh
  deposit wallet server-side. A human logging into polymarket.com once bypasses this
  (existing Safe/proxy wallets skip deployment entirely), but that defeats the
  "never leave Telegram" product requirement — not viable for real users.
- **Fix:** pass `api_key=` to `SecureClient.create()`. `polymarket.auth.ApiKey` is
  `BuilderApiKey | RelayerApiKey`. We're using `RelayerApiKey(key=..., address=...)`
  — a UUID key + the Ethereum address of whoever owns the key (not per-user; traced
  in `_internal/actions/relayer/auth.py`, it sends static `RELAYER_API_KEY` /
  `RELAYER_API_KEY_ADDRESS` headers on every relayer request regardless of which
  user's wallet is being deployed). It's a backend-level credential like the
  Telegram bot token, not a per-trade secret — stored in `.env` as
  `RELAYER_API_KEY` / `RELAYER_API_KEY_ADDRESS`, obtained from
  polymarket.com Settings > API Keys (requires a one-time human dashboard login,
  done once by the builder, not per user).
- `place_trade.py` now builds this into `api_key=` when both env vars are set.
- Still needs a live end-to-end run: Jayden supplies a **fresh, never-shared**
  private key via his own shell env (never through chat — an earlier key was pasted
  into chat and must be treated as burned) to confirm auto-deployment + trade +
  builder attribution all work together.

## 9. pUSD and the bridge: how deposits/withdrawals actually work (confirmed 2026-07-03)

- **Deposit wallets do not hold USDC. They hold pUSD**, Polymarket's collateral token
  (`PRODUCTION.collateral_token = 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB`).
  The PRD's "deposit USDC to your Safe address" mental model is one generation old.
- **Deposits:** `POST https://bridge.polymarket.com/deposit {address}` returns
  personal bridge addresses (`evm`, `svm`, `btc`). User sends USDC (or other
  supported assets — see `/supported-assets`) to their bridge address on any
  supported chain; it arrives as pUSD in the deposit wallet. **This gives Fade
  cross-chain deposits (PRD F-6) almost for free, including Solana and Bitcoin.**
- **Withdrawals:** `POST /withdraw {address, toChainId, toTokenAddress,
  recipientAddr}` returns a bridge address; transfer pUSD to it (gasless via
  `client.transfer_erc20`, needs the Relayer API key) and the bridge delivers the
  destination token to the recipient. Withdrawal = 2 steps: bridge request + pUSD
  transfer.
- Bridge endpoints take an optional `X-Builder-Code` header for attribution — set it.
- Balance check: `client.get_balance_allowance(asset_type="COLLATERAL")`, base units.
- Deposit detection must watch **pUSD** transfers into the deposit wallet (bridge
  delivers pUSD), not just USDC — `watch_deposits.py` updated accordingly.
- **Geoblock scope:** trading (order placement) is geoblocked by IP in close-only
  regions (incl. US); the geoblock doc says close-only still allows closing/selling,
  and withdrawals/deposits are wallet operations, not CLOB orders. Round-trip test
  should work from Jayden's location; the live BUY cannot.
- `wallet_round_trip.py` implements: `info` (wallet + pUSD balance),
  `deposit-address` (personal bridge addresses), `withdraw` (bridge request +
  gasless pUSD transfer with confirm step).

## 10. Open items for Phase 1

- **Wallets module:** build `/start` → generate per-user signer, call
  `SecureClient.create(private_key=..., api_key=<Fade's RelayerApiKey>)` to
  auto-deploy their deposit wallet, store signing credentials behind the
  `wallets/` signing interface (never in the app DB in plaintext, per CLAUDE.md).
- Native-USDC deposits need a swap/convert decision.
- Verify builder attribution actually credits after the first real trade (task 3 run).
