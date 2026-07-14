# Fade — Handoff

Rewritten 2026-07-14 (Stage 16 docs truth pass), for whoever (including
future-me) picks this up cold. The previous version of this file described
the world as of Stage 7 and had gone badly stale — it claimed 142 tests and
that F-23/F-24/F-26 were "not started." All of that is obsolete below.

Read this first, then [PRD.md](PRD.md) (spec), [DECISIONS.md](DECISIONS.md)
(D-1 to D-13), [SPIKE-NOTES.md](SPIKE-NOTES.md) (SDK findings),
[ROADMAP.md](ROADMAP.md) (Stages 4–9, all complete),
[ROADMAP-PART3.md](ROADMAP-PART3.md) (Stages 10–15, all complete), and
[ROADMAP-PART4.md](ROADMAP-PART4.md) (Stages 16–21: 16 is this stage;
17–21 are the queued future work).

Current state: `232 passed`, `uv run ruff check .` clean, on top of commit
`9c9637c` (per-stage commits exist for Stages 8–15; Stage 16 is in the
working tree at the time of writing). No test is skipped, xfailed, or
known-failing — verify with `grep -rn "skip\|xfail" tests/` as always.

## 1. Where we actually are

Every stage in every roadmap through Part 3 is DONE. That means:

- PRD Phases 0–3 (spike, solo core, social core, hardening) — reports in
  SPIKE-NOTES.md, PHASE1-REPORT.md, PHASE2-REPORT.md, commit `a2ec8ea`.
- ROADMAP.md Stages 4–9: production readiness, safety rails, resolutions,
  clan league + /record, flex cards, head-to-head challenges.
- ROADMAP-PART3.md Stages 10–15: withdrawal-path security (cooldown,
  EIP-55, export auto-delete, owner locks), background-job resilience,
  error reporting + F-26 digest, UX completion, social/discovery polish,
  and the flag-gated Mini App custody slice.
- ROADMAP-PART4.md Stage 16 (this stage): initData freshness, client-custody
  UX degradation, callback byte safety, SRI pins, this rewrite.

**Still not done, and still the most important thing: Stage 0.** No real
second human has ever used this bot. Every test runs against mocks. One
friend in one group chat will teach more than any further stage.

## 2. F-number status (all of them)

| Req | Status | Where |
|---|---|---|
| F-1 onboarding/attestation | ✅ | `bot/handlers/onboarding.py` (+ Mini App branch, flag-gated) |
| F-2 `/wallet` | ✅ | balance + open-position value + deposit info. QR still missing (PRD says "QR + copyable") — Stage 17 |
| F-3 deposit detection | ✅ | `bot/handlers/deposits.py`, per-item failure isolation |
| F-4 `/withdraw` | ✅ | + D-13 first-time-address cooldown, EIP-55 rendering/validation, receipt rows (`withdrawals` table) |
| F-5 `/export` | ✅ | + 5-min self-delete; client-custody wallets get the honest "no server key" branch |
| F-6 cross-chain deposits | ✅ | via the pUSD bridge (SPIKE-NOTES §9) |
| F-7 DM-only wallet cmds | ✅ | group redirects; deep-link payloads now route (Stage 13) |
| F-8 paste-URL trading | ✅ | `bot/handlers/trade.py` |
| F-9 `/trending` `/search` + categories | ✅ | category buttons on /trending (Stage 14); byte-safe callback data (Stage 16) |
| F-10 FOK + slippage guard | ✅ | guard works; USER-facing slippage control still missing — Stage 17 |
| F-11 builder code | ✅ | every order |
| F-12 `/positions` | ✅ | owner-locked sell cards; full-position sells only (partials — Stage 17) |
| F-13 confirmations | ✅ | builder fee + estimated Polymarket platform fee itemized (Stage 13) |
| F-14 resolution notify | ✅ | crown/roast + flex card, isolated per-user failures |
| F-16–F-20 clans/broadcast/leaderboard | ✅ | + Forbidden→deactivation cleanup (Stage 11) |
| F-21 clan league | ✅ | skips deactivated clans |
| F-22 `/record` | ✅ | |
| F-23 flex cards | ✅ | Stage 8; Pillow, brand palette, idempotent DM |
| F-24 `/challenge` | ✅ | Stage 9 + Stage 14 (text_mention opponents, challenger withdraw) |
| F-25 kill switch | ✅ | withdrawals always exempt |
| F-26 metrics/digest | ✅ | Stage 12: daily digest + `/stats`, Sentry/admin-DM error reporting |
| §7.8 Mini App custody | 🟡 | Engineering slice (Stage 15): flag-gated onboarding + registration with client-side keygen; trading/withdrawals for client wallets deliberately refuse until the signing round-trip (Stage 21) |

Commands beyond the PRD's list: `/help`, `/security`, `/privacy`, `/stats`
(admin), `/pause` `/resume` (admin), `/ping`. The `COMMANDS` registry in
`bot/handlers/basic.py` is the single source of truth for /help, onboarding,
and Telegram's "/" menu.

## 3. Deviations of record (unchanged, still true)

1. `polymarket-client` (py-sdk beta) replaces the archived `py-clob-client`.
2. "Safe" in the PRD = Polymarket's Deposit Wallet (ERC-1967 beacon proxy).
3. Relayer API key ≠ Builder API key: the Builder key relays for users.
4. D-11: the $100 deposit cap was explicitly waived by Jayden — don't re-add.
5. F-13's platform fee is an ESTIMATE from the market's fee params (the fill
   response carries no fee figures; the beta SDK has no public accessor, so
   `TradingService._platform_fee_info` reaches an internal seam, degrading
   to an honest "estimate unavailable" if that seam moves).
6. Brand voice is deliberately applied to F-14 resolution DMs (PRD wants it).
7. Challenge opponents lock by username (or by id for text_mention users);
   a renamed opponent can't accept a pending username-challenge — the
   challenger's Withdraw button and the 24h TTL guarantee nothing is stuck.

## 4. Known accepted quirks (carried forward, all still true)

- **`Resolution.resolved_at` is detection time, not on-chain time.** If the
  bot is down across a Sunday 18:00 ET boundary, a resolution can land in
  the wrong week for leaderboard/league purposes.
- **The Sunday league job checks weekday daily at 18:00 ET** and has still
  never fired against a real Sunday in production.
- **The broadcast queue is in-memory**: pending (unsent) messages are lost
  on restart. Bounded blast radius, accepted.
- **`POLYGON_RPC_URL` defaults to a free provider** (1rpc.io, 50-block
  eth_getLogs chunks). DEPLOYMENT.md §2 mandates a keyed provider in prod.
- **`fade_events` history starts at Stage 7's migration** — older
  fades/copies are invisible to `/record`'s "most faded" stat.
- **`compute_league` PnL% is volume-weighted** (total_pnl / total_volume).
- **Mini App scripts are CDN-loaded with SRI pins** (Stage 16). Telegram
  serves telegram-web-app.js UNVERSIONED, so its pin will fail closed when
  Telegram updates it — refresh the hash in `src/fade/miniapp.py` then.
  Vendoring is planned in Stage 21.
- **Client-custody registered addresses are the signer EOA**, not a derived
  deposit wallet — deferred to Stage 21 with the signing round-trip.
- **Windows dev PATH**: `uv`/`gh` came from winget; new shells may need
  the machine PATH re-read if "uv not recognized."

## 5. Known failing or skipped tests

**None.** Two fixture corrections of record (not weakenings): the withdraw
tests' DEST constant became its valid EIP-55 form when checksum validation
landed (Stage 10), and the digest tests seed clock-relative rows because
/stats uses the real clock (Stage 16 — the old hard-coded date rotted
within a day, exactly as hard-coded dates do).

## 6. The exact next steps

1. **Stage 0. Really.** One real friend, one real group, one real trade,
   one real Fade tap. Before Stage 18 (referrals) at the absolute latest.
2. **Deploy for real** (DEPLOYMENT.md): VPS + Postgres + webhook +
   `FADE_WITHDRAWAL_COOLDOWN_MINUTES=60` + keyed RPC + SENTRY_DSN. All
   code-complete, none of it ever exercised against real infrastructure.
3. **ROADMAP-PART4.md Stages 17–21** in order: trading UX completion (QR,
   /slippage, /history, partial sells), growth loop (referrals), limit
   orders, auto-copy, Mini App signing round-trip.

Whatever you do, verify state before trusting this file — check the actual
code/tests/git log first. The previous revision of this document is the
cautionary tale.
