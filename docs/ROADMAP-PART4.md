# Fade — Roadmap Part 4 (Post-Part-3 Audit: Stages 16–21)

Written 2026-07-13, after Stages 4–15 all landed (HEAD `bffc672`, 224 tests
passing, ruff clean). Every F-number in the PRD is now built or deliberately
flag-gated. This roadmap comes from a fresh audit of the whole tree —
including the code Parts 2–3 themselves added, because new code carries new
bugs — plus the PRD's own deferred-feature list.

Same rules as before: stages ordered by importance, each with a paste-ready
`/goal` block. Earlier stages fix real defects found in this audit; later
stages are the feature ladder the PRD always planned.

**Still standing, still not a coding task:** Stage 0. No real second human
has ever used this bot. Twelve stages of mocked green tests cannot substitute
for one friend tapping one real Fade button. Do it before Stage 18 makes the
growth loop shinier.

## Audit summary

**Cleared since Part 3:** all 19 findings in ROADMAP-PART3's table are done
(Stages 10–15), and the working tree is committed per-stage.

**New findings (this audit):**

| # | Finding | Severity | Stage |
|---|---|---|---|
| 1 | Mini App initData validation never checks `auth_date` — a captured initData replays forever (Telegram's docs say to bound its age) | Security | 16 |
| 2 | With the custody flag on, a client-custody user's `/wallet` and post-registration `/start` overview crash: `get_balance` → `secure_client` → `ClientCustodyError`, unhandled in those handlers | Bug (latent, flag off) | 16 |
| 3 | Category callback data `c:{name[:40]}` truncates by characters, not bytes — a non-ASCII category can exceed Telegram's 64-byte callback limit | Bug (minor) | 16 |
| 4 | Mini App page loads ethers.js/telegram js from CDNs with no subresource-integrity pins — supply-chain exposure for the page that generates keys | Security | 16 |
| 5 | docs/HANDOFF.md is badly stale (claims 142 tests, HEAD `a8c3255`, F-23/24/26 "not started"); its own last line warns against trusting it | Docs | 16 |
| 6 | PRD F-2 says `/wallet` shows the address as "QR + copyable" — no QR code exists anywhere | PRD gap | 17 |
| 7 | `users.slippage_bps` exists (default 200) and drives every order's guard, but no command lets a user see or change it | PRD gap (F-10 UX) | 17 |
| 8 | No `/history` — the orders table has every fill/rejection, users can see none of it | UX | 17 |
| 9 | Position sells are all-or-nothing; no partial sell | UX | 17 |
| 10 | PRD J1 literally opens with "taps a friend's referral link" — no referral attribution exists | Growth (PRD journey) | 18 |
| 11 | Flex cards invite sharing but carry no way for the viewer to find the bot | Growth | 18 |
| 12 | Limit orders (F-15, P2) — the PRD's designated next trading feature | Feature | 19 |
| 13 | Auto-copy trading — PRD "out of scope v1, planned later" | Feature | 20 |
| 14 | Client-custody wallets can't trade/withdraw (deliberate Stage-15 slice cut); the Mini App signing round-trip is the designed completion | Architecture | 21 |

Known-and-accepted (not scheduled): `Resolution.resolved_at` is detection
time not chain time (weekly-boundary skew documented in HANDOFF), the
broadcast queue is in-memory (pending messages lost on restart, bounded
blast radius), `POLYGON_RPC_URL` still points at a free provider in dev
(DEPLOYMENT.md §2 already mandates a keyed one for prod).

---

## Stage 16 — Post-audit hardening and the docs truth pass

**Why first:** three of the four code findings touch the security surface
Part 3 just built (the Mini App and its custody model), and the stale
HANDOFF.md is actively misleading — it fails its own "verify before
trusting" rule. Fix the things the audit caught before building anything on
top of them.

**What it delivers:** initData freshness enforcement, graceful client-custody
UX everywhere money is displayed, byte-safe callback data, SRI-pinned Mini
App scripts, and a HANDOFF.md that tells the truth again.

```
/goal Post-audit hardening and docs truth pass is complete, proven as
follows, all pasted into the conversation: (1) initData freshness:
fade/miniapp.py validate_init_data rejects initData whose auth_date is
older than a bounded window (constant, suggest 1 hour; justify your choice
in the code) or missing/non-numeric — a test proves stale initData is
rejected and fresh initData accepted using an injectable clock, and the
existing signature tests keep passing unchanged. The register endpoint must
therefore reject a replayed old initData with 401 — a test proves it.
(2) Client-custody UX: with FADE_MINIAPP_CUSTODY on, a registered
client-custody user running /wallet, /start (overview), and /positions gets
a coherent response with no unhandled exception: balance/deposit info
renders what is knowable without server signing (the positions API is
public; balance via get_balance is not — degrade that line honestly, e.g.
"balance: check your wallet app — Fade can't read client-custody wallets
yet"), and a plain pointer to what this custody mode can and can't do — a
test per command proves no exception propagates and the degraded copy
renders. The flag-off path must remain byte-identical (existing tests
prove it by passing unchanged). (3) Callback-data byte safety: category
callback data is truncated by UTF-8 BYTES (Telegram's 64-byte limit), not
characters — a test proves a long non-ASCII category name produces
callback data whose UTF-8 encoding is within limits and still round-trips
to a working category tap. (4) Mini App script integrity: the two external
<script> tags in MINIAPP_HTML carry subresource-integrity (integrity= +
crossorigin=) pins for exact versions — a test proves both script tags have
integrity attributes. Record the pinned versions plainly in the module
docstring. (5) docs/HANDOFF.md is rewritten to be true as of this stage:
current HEAD, current test count, the real done/partial status of every
F-number (F-23/F-24/F-26 are DONE; Mini App custody is flag-gated slice),
pointers to ROADMAP-PART3.md (complete) and ROADMAP-PART4.md (this file),
and a "known accepted quirks" section carrying forward what remains true
from the old §4 — full contents pasted into the conversation. (6) `uv run
pytest -q` exits 0 with the full suite passing and `uv run ruff check .` is
clean. Constraints: do not weaken or delete any existing test; do not modify
docs/PRD.md or CLAUDE.md; no live network calls in tests; key material never
in logs, DB, request bodies, or fixtures beyond obviously-fake constants.
Stop after 40 turns or 4 hours and report status honestly if the condition
is not met.
```

---

## Stage 17 — Trading UX completion: QR, slippage, history, partial sells

**Why second:** every item here is a promise the product already makes.
F-2's own text says "QR + copyable"; the slippage guard protects every order
with a number the user can't see or change; the orders table remembers
everything while the user can see nothing; and one-tap Sell currently means
all-or-nothing. All four are small, none is risky, together they make the
daily trading loop feel finished.

**What it delivers:** a QR code in /wallet, /slippage view-and-set, /history,
and partial sells.

```
/goal Trading UX completion (QR, slippage, history, partial sells) is
complete, proven as follows, all pasted into the conversation: (1) /wallet
QR (PRD F-2 as written): the DM wallet view attaches a QR code image of the
user's EVM deposit address (generate with a well-maintained pure-Python
library, e.g. qrcode with the Pillow already in the tree; record the
dependency plainly) — a test proves the QR generation function returns
valid non-empty image bytes decodable as a real image, a test proves the
encoded payload is exactly the deposit address (decode the QR in the test
or test the data pipeline into the encoder, same discipline as the flex-card
tests), and a test proves /wallet sends it as a photo alongside the existing
text in DM and that NOTHING changes for the group-redirect path. (2)
/slippage: shows the user's current guard in plain language (bps and as a
percentage) and lets them pick a new value from preset buttons (e.g. 0.5% /
1% / 2% / 5%) writing users.slippage_bps — a test proves the current value
renders, a test proves a preset tap persists and subsequent orders use it
(assert through TradingService._slippage_bps or an executed mock order),
and a test proves bounds: values outside a sane floor/ceiling are not
offered. DM-only or group-safe at your discretion, but it must never render
balances or addresses. Money screen: plain copy. (3) /history: the user's
last N (10) orders newest-first — side, market title (sanitized), amount,
status, fill price where filled — from the existing orders table, DM-only
with the standard group redirect — a test proves rendering from seeded
orders including a rejected one, and a test proves another user's orders
never appear. (4) Partial sells: the /positions sell flow offers 25% / 50% /
100% (and keeps today's full-sell as the 100% path) — a test proves a 50%
tap executes a SELL for half the shares (share-precision handling stated
and tested), a test proves 100% matches today's behavior, and the existing
owner-lock still applies to the new buttons (test). (5) `uv run pytest -q`
exits 0 with the full suite passing and `uv run ruff check .` is clean, and
the COMMANDS registry in basic.py gains /slippage and /history (test:
/help contains them — the existing registry test should force this).
Constraints: do not weaken or delete any existing test; do not modify
docs/PRD.md or CLAUDE.md; no live network calls in tests; money screens
plain; group-safety rules on every new render path. Stop after 45 turns or
4 hours and report status honestly if the condition is not met.
```

---

## Stage 18 — Growth loop: referrals and shareable entry points

**Why third:** PRD J1's very first line is "Taps a friend's referral link" —
the journey the whole funnel starts with was never built. And the flex card
(the one artifact designed to leave the group chat) gives its viewer no way
to find the bot. This stage makes sharing convert. Do Stage 0 (real users)
before or during this one — a growth loop with zero humans in it is a
flywheel in a vacuum.

**What it delivers:** referral deep links with attribution, an /invite
command, and flex cards that carry a join link.

```
/goal Growth loop (referrals + shareable entry points) is complete, proven
as follows, all pasted into the conversation: (1) Referral attribution
(PRD J1): /start deep-link payloads of the form ref<id> attribute the new
user to the referrer — users gains a nullable referred_by column
(migration), set ONLY when a brand-new user arrives with a valid referrer id
(existing users are never re-attributed, self-referral is ignored, a
nonexistent referrer id is ignored) and persisting through the attestation
gate so the attribution survives to wallet creation — tests prove: new user
via ref link is attributed after completing attestation; existing user is
not re-attributed; self-referral no-ops; the routing payloads Stage 13 added
(withdraw/positions/etc.) keep working unchanged. (2) /invite: DMs the user
their personal referral link (t.me/<bot>?start=ref<their id>) with
brand-voice copy and their current referral count (count of users
referred_by them who completed attestation) — a test proves the link
contains their id and the count is computed correctly from seeded users.
(3) Flex cards carry an entry point: the flex-card DM caption (and the
settlement/crown moments at your discretion) includes the user's referral
link so a shared screenshot's owner can answer "what app is that" with one
tap — a test proves the caption contains the link. Rendering stays within
group-safety rules: referral links contain user ids, which are fine (they
are not addresses or balances), but confirm no wallet data rides along
(test asserts no "0x" in the caption). (4) /record or /stats surfaces
referrals: your call which (justify), showing the user's successful-referral
count — a test proves it. (5) The COMMANDS registry gains /invite (the
existing /help test must force it). (6) `uv run pytest -q` exits 0 with the
full suite passing and `uv run ruff check .` is clean, and docs/DECISIONS.md
gains a D-number entry recording the referral design (attribution rules,
why attestation-completion is the counting event). Constraints: do not
weaken or delete any existing test; do not modify docs/PRD.md or CLAUDE.md;
no live network calls in tests; no rewards/points/token mechanics of any
kind (PRD explicitly defers them — this is attribution and links only).
Stop after 45 turns or 4 hours and report status honestly if the condition
is not met.
```

---

## Stage 19 — Limit orders (PRD F-15)

**Why fourth:** the PRD's designated P2 trading feature and the most-request
candidate from any real trader (Priya persona): "buy YES at 55¢ or better,
not at whatever the book says right now." It's also the first feature that
adds standing state to the CLOB relationship (open orders that outlive the
command), which is why it comes after the hardening stages, not before.

**What it delivers:** limit order placement from the market card, /orders to
list and cancel open orders, and fills reported when they happen.

```
/goal Limit orders (PRD F-15) are complete, proven as follows, all pasted
into the conversation: (1) Placement: the market card (paste-URL and
discovery flows) offers a "Limit" path alongside the existing market-order
buttons: pick side -> enter limit price (validated against tick size and
0-1 bounds) -> enter size -> plain confirm screen -> placed via the SDK's
limit-order API through the existing TradingService (extend it; do not
build a parallel execution path) with the SAME idempotency
(telegram_update_id), builder-code attribution, kill-switch, and daily-cap
treatment as market orders — tests prove: a placed limit order records an
Order row with its price and a distinguishable order type; a webhook retry
cannot double-place (idempotency test); the kill switch blocks placement;
the volume cap counts limit BUYs. (2) /orders: lists the user's open limit
orders (from the CLOB via a mocked client in tests) with one-tap Cancel —
tests prove listing renders seeded open orders, cancel calls the SDK cancel
for the right order id and confirms plainly, and another user cannot cancel
someone else's order via a forged callback (owner-lock pattern). (3) Fill
notification: a background check (same job/scanner discipline as deposits/
resolutions: restart-safe, idempotent, per-item error isolation from Stage
11) DMs the user when an open limit order fills — a test proves exactly-one
notification per fill across a simulated restart, and a test proves one
user's failure doesn't block others. Filled limit orders flow into the same
Order-row lifecycle the leaderboard already reads (test: a filled limit BUY
appears in volume). (4) Group broadcasting: a FILLED limit order broadcasts
to clans exactly like a market fill (reuse broadcast_fill; test proves the
card posts with Fade/Copy buttons); mere placement does not broadcast.
(5) /help and the COMMANDS registry gain /orders. (6) `uv run pytest -q`
exits 0 with the full suite passing and `uv run ruff check .` is clean;
docs/DECISIONS.md gains a D-number entry for scope choices (e.g. expiry
handling, partial-fill display). Constraints: do not weaken or delete any
existing test; do not modify docs/PRD.md or CLAUDE.md; no live network in
tests; reuse TradingService and the existing card store. Stop after 50
turns or 5 hours and report status honestly if the condition is not met.
```

---

## Stage 20 — Auto-copy trading

**Why fifth:** the PRD's "planned later" social feature: turn the one-tap
copy into a standing "mirror this member" rule. It's the natural deepening
of the product's core loop, and it's deliberately AFTER limit orders and the
hardening stages because a standing rule that trades your money while you
sleep demands the safety rails be proven first (they now are: daily caps,
anomaly alerts, kill switch, per-item isolation).

**What it delivers:** /autocopy to mirror a clan member with hard limits,
automatic execution when they trade, and unmistakable transparency.

```
/goal Auto-copy trading is complete, proven as follows, all pasted into the
conversation: (1) Setup: /autocopy @user <per-trade-size> in a group (or
from a broadcast card button — your call, justify) creates a standing rule:
mirror that member's future BUYS with a fixed per-trade USDC size chosen by
the copier — a migration adds the rules table (copier, target, chat, size,
active, created_at); tests prove rule creation, an owner-lock (only the
copier manages their rule), and a hard cap of active rules per user (pick
and justify, suggest 3). (2) Execution: when a target's BUY fills and
broadcasts, active copiers' mirror orders execute through
TradingService.execute_market_order with everything that implies (FOK,
slippage guard, builder code, idempotency via a synthesized-but-unique
update id scheme you document, kill switch, daily volume cap) — tests
prove: a target fill triggers exactly one mirror order per active copier
sized per their rule; a copier at their daily cap is skipped with a DM
explaining why (not silently); a mirror failure for one copier never blocks
other copiers (Stage 11 isolation discipline); pausing trading blocks
mirrors. (3) Transparency: every mirrored execution DMs the copier a plain
confirmation AND posts the standard broadcast card so the group sees it
(reuse broadcast_fill); the rule's existence is visible to the copier via
/autocopy with no arguments (lists their rules with one-tap disable) —
tests prove the DM, the broadcast, listing, and disabling (disabled rules
stop mirroring immediately — test). (4) Safety: rules auto-disable after N
consecutive mirror failures (pick and justify) with a DM to the copier —
test proves it. (5) /help registry gains /autocopy; `uv run pytest -q`
exits 0 with the full suite passing and `uv run ruff check .` is clean;
docs/DECISIONS.md gains a D-number entry (size model, caps, disable
policy). Constraints: do not weaken or delete any existing test; do not
modify docs/PRD.md or CLAUDE.md; no live network in tests; SELLS are never
mirrored in this slice (exiting is always a human decision — state this in
the user-facing copy); no new order-execution path outside TradingService.
Stop after 50 turns or 5 hours and report status honestly if the condition
is not met.
```

---

## Stage 21 — Mini App signing round-trip (complete PRD §7.8)

**Why last:** it completes the custody story Stage 15 opened — client-custody
wallets that can actually trade and withdraw, with order payloads signed on
the user's device. It is the largest remaining lift, changes the interaction
model (trades need the Mini App in the loop), and deserves real-user
feedback on the flag-gated onboarding slice first. When this lands, the
Polycule-class server-compromise risk is structurally gone for client
wallets, not just mitigated.

**What it delivers:** in-Mini-App signing for orders and withdrawals from
client-custody wallets, proper deposit-wallet derivation, and vendored
crypto assets.

```
/goal Mini App signing round-trip (PRD §7.8 completion) is complete, proven
as follows, all pasted into the conversation: (1) Signing flow: a
client-custody user confirming a trade (or withdrawal) receives an inline
web-app button that opens the Mini App with a pending-action reference; the
page loads the pending action's exact payload from the server, displays it
plainly (market, side, size / destination, amount), signs it locally with
the stored client key, and submits ONLY the signature + action reference
back; the server verifies the signature matches the wallet's registered
signer address before submitting to the CLOB/relayer — tests prove: a
pending action is created with a bounded TTL and single-use semantics (a
second submission of the same reference is rejected — idempotency); a
signature that doesn't verify against the registered signer is rejected 401
with no order placed; a valid signed action executes through the same
TradingService/WalletService paths with builder code and guards intact
(mock the signature verification inputs with obviously-fake test vectors —
pure crypto, no network). (2) The Stage 15 refusals are replaced: for
client-custody wallets, trading and withdrawal paths now route into the
signing flow instead of ClientCustodyError — a test proves the old
"not supported" message is gone for flag-on client wallets and the pending-
action flow engages; server-custody wallets are completely unaffected
(existing tests pass unchanged). (3) Deposit-wallet derivation: registered
client wallets store the properly derived Polymarket deposit-wallet address
for their signer (the SDK's derivation, configured for the production
environment), not the bare EOA — a test proves the derivation function is
called with the registered signer and its result is what gets stored;
existing client-custody rows migrate or are handled explicitly (state how).
(4) Asset hygiene: the Mini App page's crypto dependencies are served from
our own FastAPI app (vendored files with hashes recorded) OR remain CDN
with SRI pins AND a documented justification — state which and why.
(5) SECURITY.md's custody-modes section is updated: client custody now
supports trading/withdrawals, with the signing model described honestly —
contents pasted. (6) `uv run pytest -q` exits 0 with the full suite passing
and `uv run ruff check .` is clean. Constraints: do not weaken or delete
any existing test; do not modify docs/PRD.md or CLAUDE.md; no live network
in tests; key material never touches the server in any code path added
here — signatures travel, keys never do; the flag remains off by default.
Stop after 60 turns or 6 hours and report status honestly if the condition
is not met.
```
