# Fade — Resolved Design Decisions

Answers to the PRD's open questions and under-specified points, decided by Jayden
(2026-07-03) before the build started. These override nothing in the PRD; they fill
its gaps. If a decision here conflicts with the PRD, flag it instead of guessing.

## D-1. Custody & signing architecture (PRD §7, §13.1)
pvp.trade pattern adapted to Polymarket's builder stack:
- Each user gets their own Polygon **Safe** that holds funds and positions. No pooled
  hot wallet, no custodial storage of withdrawal-capable private keys.
- The backend holds only **trade-scoped order-signing credentials** (place/cancel CLOB
  orders). It can never move funds to an arbitrary address.
- Gas via Polymarket's **Relayer** (gasless UX).
- Signing infra is built behind the `wallets/` signing interface. Dev/tests use a local
  encrypted-at-rest implementation with obviously fake keys; managed infra (Turnkey)
  is evaluated during the Phase 0 spike per PRD §13.1 and can be swapped in behind the
  same interface.

## D-2. Broadcast privacy default (PRD §13.3, F-17)
**Show dollar amounts by default** (pvp.trade-style, more fun). Per-user toggle to hide
amounts remains (F-17).

## D-3. Slippage guard (F-10)
**User-configurable** slippage tolerance, stored per user. Default 2% until the user
changes it (e.g. `/slippage 1.5`). Every order still carries a worst-price limit —
configurability changes the number, never removes the guard.

## D-4. Market scope
**Binary YES/NO markets only** for v1. Pasting a URL to a multi-outcome / negRisk event
gets a polite "binary markets only for now" response, not a partial trade flow.

## D-5. Copy/Fade by users without an account (F-18)
Tapping Copy/Fade without an account → ephemeral prompt telling them to create an
account and deposit first, with a deep link to DM onboarding (`/start`). v1 does NOT
preserve the pending trade across onboarding; they come back and tap again.

## D-6. Leaderboard semantics (F-20)
- **Realized PnL = both**: realized on position sells AND on market resolutions.
- **Win rate is per trade** (each closed trade counts once), not per market.
- **Weekly reset: Sunday 18:00 US Eastern (America/New_York)**, same moment the clan-war
  results post (PRD J3 "Sunday 6pm"). One boundary for both keeps the recap and the
  reset consistent. Store timestamps in UTC; convert at the boundary.

## D-7. Attestation gate copy (F-1, §10)
Pure self-attestation, no enumerated jurisdiction list in our copy (the list is
Polymarket's and changes). The gate requires ticking through:
1. "I am 18 or older."
2. "I am not located in, or a resident of, any jurisdiction where using Polymarket is
   prohibited (including the United States), per Polymarket's Terms of Service." —
   with a link to Polymarket's ToS.
3. "I accept Polymarket's Terms of Service and Fade's terms."
Attestation (user id + timestamp + version of the text) is stored in the DB. Plain,
precise tone — this is a money screen.

## D-8. Deposit detection (F-3)
Poll Polygon over RPC for USDC `Transfer` events to user Safe addresses, every ~30s.
Credit a deposit after **12 confirmations** (~30s of blocks). No third-party webhook
dependency in v1. Confirmation DM is plain-voice per brand rules.

## D-9. Kill switch & admin identity (F-25)
- Admin = a single **numeric Telegram user ID** from env var `FADE_ADMIN_USER_ID`.
  Handles (@handsomedog385) are mutable and MUST NOT be used for auth; Jayden needs to
  supply the numeric ID (e.g. via @userinfobot) before prod.
- `/pause` and `/resume` DM commands, admin-only, backed by a DB flag so the state
  survives restarts. While paused: all order placement rejected with a clear message;
  withdrawals, `/wallet`, `/positions` (read-only), and `/export` keep working (F-25,
  PRD §7.3).

## D-10. Fee display (F-13, §8)
Builder fee (1%, computed and paid natively by Polymarket at match time) is **computed
locally for display only**. Order previews and confirmations show the fee explicitly
as an amount **added on top of the quoted size** (e.g. "Size: $50.00 · Fee (1%): $0.50
· Total: $50.50"). No custom fee-collection logic anywhere in the codebase.

## D-11. Deposit caps (PRD §12 Phase 2 milestone: "small deposit caps ($100/user)")
**Explicitly waived by Jayden (2026-07-07).** No per-user deposit cap is implemented
anywhere — a user can deposit any amount today. This is a deliberate scope cut, not an
oversight: do not add it back or flag it as a forgotten gap without asking first. If
closed-beta risk exposure becomes a real concern later, revisit deliberately rather
than reintroducing it as a "fix."

## D-12. Safety rails: daily volume cap + anomaly alert (PRD §7.7, §6)
Decided 2026-07-10 during the safety-hardening stage. Note this is a *trade-volume*
ceiling, distinct from the *deposit* cap waived in D-11 — a user's own funds are their
own, but how fast the backend spends them on their behalf is a blast-radius control.

- **Per-user daily trade-volume cap = $1000 / rolling 24h of BUYS** (env var
  `FADE_DAILY_VOLUME_CAP_USD`, default `1000`; set `0` to disable). Chosen to sit an
  order of magnitude above a normal Marcus-sized session ($10–50 trades) so it never
  annoys a real user, while capping how much a compromised backend or a runaway loop
  can churn through one account in a day. **BUYS only** — sells and withdrawals are
  never capped, same principle as the kill switch: you can always reduce risk / exit,
  never be trapped into more of it. Enforced before the CLOB and before the idempotency
  row, so a capped attempt is a true no-op.
- **Anomaly alert threshold = 24h volume > max(5× prior-week daily average, $200).**
  The 5× multiplier catches a genuine break from a user's own pattern; the $200 floor
  stops new-user / low-history noise (without it, a first-ever $30 trade would look
  infinitely above a $0 baseline). Alerts DM `FADE_ADMIN_USER_ID`, at most once per
  incident (re-arms once the user's 24h volume falls back under threshold), and an
  alerting failure never blocks the trade. These numbers are deliberately conservative
  starting points — tune against real traffic, they're not load-bearing guarantees.
