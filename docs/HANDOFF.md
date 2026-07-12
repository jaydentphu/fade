# Fade — Handoff

Written 2026-07-10, for whoever (including future-me) picks this up cold.
Read this first, then [PRD.md](PRD.md) (spec), [DECISIONS.md](DECISIONS.md)
(resolved ambiguities, D-1 to D-12), [SPIKE-NOTES.md](SPIKE-NOTES.md) (SDK
findings), [ROADMAP.md](ROADMAP.md) (what's queued next). This file is the
"what's actually true right now" snapshot; those are the "why" and "what's
planned."

Current state: `142 passed`, `uv run ruff check .` clean, HEAD at `a8c3255`.
No test is skipped, xfailed, or known-failing — verified via
`grep -rn "@pytest.mark.skip\|xfail" tests/` returning nothing.

## 1. Where we actually are

The PRD's own Phase 0–3 ([PRD.md](PRD.md) §12) are done and each has a report:
[SPIKE-NOTES.md](SPIKE-NOTES.md) (Phase 0), [PHASE1-REPORT.md](PHASE1-REPORT.md)
(Phase 1), [PHASE2-REPORT.md](PHASE2-REPORT.md) (Phase 2), and Phase 3's proof
is pasted in the conversation history (isolation/kill-switch/idempotency/
sanitization audits + `/security`, commit `a2ec8ea`).

After Phase 3, there was no more PRD-numbered phase to run, so I wrote
[ROADMAP.md](ROADMAP.md) — a self-authored, priority-ordered backlog of
remaining PRD features (F-14, F-21, F-22, F-23, F-24) plus real operational
gaps the PRD assumes but nothing had built (webhook mode, safety rails). Its
"Stages" are **not** PRD phases — they're my own sequencing. Done so far:

| Stage | What | Commit |
|---|---|---|
| 4 — Production deployment readiness | Webhook receiver, Postgres-ready, durable UI-card storage, pluggable error reporter | `3426cd6` |
| 5 — Safety hardening | Daily volume cap, global broadcast rate cap, anomaly alert | `f2d3160` |
| 6 — Resolution lifecycle (F-14) | Crown/roast DMs, leaderboard PnL completion | `aa752d9` |
| 7 — Clan growth (F-21, F-22) | Weekly clan-vs-clan league, `/record` | `a8c3255` |

**Not started:** Stage 8 (flex cards, F-23), Stage 9 (head-to-head
challenges, F-24), and — this is the important one — **Stage 0: a real human
has never used this bot.** Every one of the 142 tests runs against mocks.
Nothing in this codebase has been exercised by an actual second Telegram
user tapping a real button. See §6.

One doc bug to know about: [ROADMAP.md](ROADMAP.md)'s Stage 8 and 9 `/goal`
text internally say "hooking into the Stage 3 resolution job" — that's stale
from before the stages were renumbered 1→4-9. It means Stage 6 (resolutions,
now done). If you paste those `/goal` blocks verbatim, mention the
renumbering or Claude will look for a nonexistent "Stage 3."

## 2. Done vs. stubbed, by PRD requirement

| Req | Status | Note |
|---|---|---|
| F-1 onboarding/attestation | ✅ Done | `src/fade/bot/handlers/onboarding.py` |
| F-2 `/wallet` | 🟡 Done, incomplete | Shows balance + deposit info; **omits open-position value** (documented gap, [PHASE1-REPORT.md](PHASE1-REPORT.md)) |
| F-3 deposit detection | ✅ Done | `src/fade/bot/handlers/deposits.py` — polls Polygon, 12-confirmation, restart-safe |
| F-4 `/withdraw` | ✅ Done | `src/fade/bot/handlers/withdraw.py` |
| F-5 `/export` | ✅ Done | `src/fade/bot/handlers/export.py` |
| F-6 cross-chain deposits | ✅ Done, incidentally | The pUSD bridge gives EVM/Solana/Bitcoin/Tron deposit addresses for free — see [SPIKE-NOTES.md](SPIKE-NOTES.md) §9 |
| F-7 DM-only wallet cmds | ✅ Done | Tested per-handler; group taps redirect via deep link |
| F-8 paste-URL trading | ✅ Done | `src/fade/bot/handlers/trade.py` |
| F-9 `/trending` `/search` | 🟡 Done, minimal | `src/fade/bot/handlers/discover.py` — no category browsing (PRD says "browsable by category") |
| F-10 FOK + slippage guard | ✅ Done | `src/fade/trading/service.py` |
| F-11 builder code | ✅ Done | Attached to every order |
| F-12 `/positions` | ✅ Done | `src/fade/bot/handlers/positions.py` |
| F-13 confirmations | 🟡 Done, partial | Shows builder fee; **Polymarket's separate platform fee is not itemized** — see §3 |
| F-14 resolution notify | ✅ Done | `src/fade/bot/handlers/resolutions.py` (Stage 6) |
| F-16 `/setup` clan | ✅ Done | `src/fade/bot/handlers/clan.py` |
| F-17 broadcasts + privacy | ✅ Done | `src/fade/social/broadcast.py` + `/privacy` |
| F-18 Fade/Copy buttons | ✅ Done | `clan.py::fade_copy_callback` |
| F-19 rivalry reply | ✅ Done | Threaded reply on the original card |
| F-20 `/leaderboard` | ✅ Done | `src/fade/social/leaderboard.py` |
| F-21 clan-vs-clan league | ✅ Done | `src/fade/social/league.py` (Stage 7) |
| F-22 `/record` | ✅ Done | `src/fade/social/record.py` + `src/fade/bot/handlers/record.py` (Stage 7) |
| F-23 flex cards | ❌ Not started | Stage 8 in ROADMAP.md |
| F-24 `/challenge` | ❌ Not started | Stage 9 in ROADMAP.md |
| F-25 kill switch | ✅ Done | `src/fade/bot/handlers/admin.py`; audited Phase 3 |
| F-26 metrics/digest | ❌ Not started, not even roadmapped | See §6 |

Out-of-scope-for-v1 PRD items (limit orders, auto-copy, whale alerts, charts,
Mini App, points/token, fiat on-ramp) are still correctly untouched — no
deviation there.

**Infrastructure beyond F-numbers:** webhook receiver and Postgres support
are code-complete and unit-tested (`src/fade/webhook.py`,
`src/fade/db/session.py`) but **have never run against a real server or a
real Postgres instance** — only SQLite test fixtures. The error reporter
(`src/fade/errors.py`) defaults to `NullErrorReporter`; nothing is actually
wired to Sentry or any alerting service. `docs/DEPLOYMENT.md` documents how
to do both; nobody has done them yet.

## 3. Deviations from PRD.md / CLAUDE.md, and why

1. **CLAUDE.md stack line said `py-clob-client`; we use `polymarket-client`.**
   `py-clob-client` is archived and non-functional (confirmed by fetching its
   GitHub repo — "This client is no longer functional"). Its official
   successor, `polymarket-client` (py-sdk, beta), has native V2 builder-code
   support that `py-clob-client` never got. CLAUDE.md's stack line was
   updated with the user's implicit sign-off at the time (commit history);
   full findings in [SPIKE-NOTES.md](SPIKE-NOTES.md) §1.

2. **PRD's "Polygon Safe" custody model vs. actual implementation.** The PRD
   (and D-1) describe user funds sitting in a "Safe." Polymarket's real V2
   infrastructure uses its own **Deposit Wallet** standard — an ERC-1967
   proxy behind a Beacon contract (`derive_beacon_deposit_wallet_address` in
   the SDK), not a Gnosis Safe. Functionally equivalent (per-user smart
   contract wallet, backend never holds withdrawal keys), but the PRD's
   literal wording is out of date. Nobody has gone back to reword the PRD —
   per CLAUDE.md, PRD.md is not to be edited by me; this note is the
   correction of record.

3. **Relayer API key ≠ Builder API key (discovered live, not in any doc
   before this project).** The original assumption (D-1) was that one
   Relayer API key would let the backend deploy every user's wallet
   gaslessly. Live testing proved otherwise: a `RelayerApiKey` only
   authorizes gasless transactions **from its own owner's address** — it
   failed with `RequestRejectedError: from 0x33bB... does not match auth
   0xd2b5...` when used for a different user's signer. The fix was
   Polymarket's separate **Builder API key** (HMAC key/secret/passphrase
   triple from the builder dashboard), which *does* authorize relaying on
   behalf of other users. `src/fade/wallets/relayer.py` prefers the Builder
   key when configured, falling back to the Relayer key otherwise. This is
   why `.env` has both `RELAYER_API_KEY` and `FADE_BUILDER_API_KEY` sets —
   they are not redundant, they solve different problems.

4. **D-11: the PRD's Phase 2 milestone text ("small deposit caps
   ($100/user)") was explicitly waived by the user**, not implemented, not
   an oversight. Recorded in [DECISIONS.md](DECISIONS.md) D-11 with an
   instruction not to silently re-add it.

5. **F-9's "browsable by category" was narrowed to top-volume + keyword
   search**, no category/tag browsing UI. Scope call made during Phase 1,
   documented in [PHASE1-REPORT.md](PHASE1-REPORT.md), not revisited since.

6. **F-13's fee display shows only the builder fee, not Polymarket's
   separate platform fee.** The platform fee uses a price-curve formula
   (`rate × (p(1-p))^exponent`) discovered in the SDK source
   ([SPIKE-NOTES.md](SPIKE-NOTES.md) §4) — it's charged by Polymarket
   regardless of builder, but our confirmation text only itemizes the 1%
   builder fee. The new-balance figure is still correct (it reflects the
   real fill), just the fee line isn't a complete breakdown.

7. **CLAUDE.md's "confirmation screens stay plain" brand-voice rule is
   deliberately NOT applied to F-14 resolution DMs.** Crown/roast copy
   (`src/fade/bot/handlers/resolutions.py`) has real personality — this is
   intentional, not an oversight: the PRD explicitly names F-14 as the one
   money-adjacent moment that should have brand voice ("roast or crown
   them"). The module docstring says so; flagging here so it isn't
   "fixed" into blandness by mistake later.

## 4. Open decisions and workarounds not written down elsewhere

These didn't get a formal DECISIONS.md entry — either because they were
small enough not to warrant one, or because they surfaced late and I'm
recording them here instead of retroactively editing that file.

- **Public RPC providers are unreliable, and the default is still a free
  one.** `polygon-rpc.com` now requires a paid key (401). `publicnode.com`
  serves `eth_blockNumber` but 403s `eth_getLogs`. `1rpc.io/matic` works but
  caps `eth_getLogs` at 50 blocks/call, so `watch_deposits.py` and
  `deposits.py`'s `Rpc` class both chunk requests into 50-block windows.
  `.env`'s `POLYGON_RPC_URL` still points at `1rpc.io` — [DEPLOYMENT.md](DEPLOYMENT.md)
  recommends a keyed provider (Alchemy/Infura) for prod, but nobody has
  switched to one.

- **Signing-isolation boundary is a judgment call, not literal.** CLAUDE.md
  says "the signing module is the ONLY code that touches key material."
  `src/fade/wallets/__init__.py::secure_client()` hands `trading/` an
  *authenticated SDK client object* (a capability), not raw key bytes — the
  key itself never leaves `wallets/signing.py`. This satisfies the spirit
  (proven by `tests/wallets/test_isolation.py`'s AST-based import check and
  the "no public method returns raw key material" test) but is a specific
  interpretation of "touches," worth knowing if a stricter boundary is ever
  wanted.

- **`UiCard` keys have no collision handling.** `src/fade/bot/cards.py`
  generates card keys with `secrets.token_hex(4)` (32 bits of entropy) and
  `key` is the table's primary key. If two cards ever collide, `put()`'s
  `session.commit()` will raise an unhandled `IntegrityError` — there's no
  try/except around it (unlike, say, the `Resolution` table's intentional
  use of a `UniqueConstraint` + caught `IntegrityError` for idempotency).
  At current traffic this is astronomically unlikely, but it's a real,
  previously unflagged latent bug, not a designed behavior.

- **`Resolution.resolved_at` is detection time, not on-chain resolution
  time.** `ResolvedPosition` (the dataclass the scanner works with) has no
  timestamp field — Polymarket's `/positions` API doesn't surface exactly
  when a market resolved, only that a position is now `redeemable`. So
  `resolved_at` is stamped when our 5-minute scanner *notices* it, not when
  it actually happened. If the bot is ever down across a Sunday-18:00-ET
  weekly boundary, a resolution could land in the wrong week for
  leaderboard/league purposes. Low-impact today, worth knowing before
  relying on weekly numbers being forensically exact.

- **The Sunday league job runs a no-op check every day, not "only on
  Sunday."** `src/fade/bot/app.py::_league_job` is registered via
  `run_daily` at 18:00 ET and internally checks `now.weekday() != 6` to bail
  on non-Sundays. This has never actually fired in practice — the dev bot
  has never stayed running across a real Sunday 18:00 ET.

- **`compute_league`'s PnL% is volume-weighted (`total_pnl / total_volume`),
  not an average of members' individual PnL%.** This was a design choice
  made in Stage 7 without a dedicated DECISIONS.md entry — one clan member
  going 10x on a $5 trade won't skew the clan's ranking the way a naive
  average would.

- **`fade_events` only has data going forward from Stage 7's migration
  (`db223944cf69`).** Fades/copies executed during Phase 2 (before this
  table existed) are invisible to `/record`'s "most faded" stat. Nobody's
  `/record` output will look wrong, it'll just undercount older history.

- **Windows dev-environment friction**, for whoever's on this machine next:
  `uv` and `gh` were installed via `winget`, and new PowerShell/Bash sessions
  don't automatically pick up the updated PATH — every command in this
  session's history that runs `uv` first does
  `$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + ...`.
  If a command says "uv not recognized," that's why.

## 5. Known failing or skipped tests

**None.** `uv run pytest -q` → `142 passed`. `grep -rn "@pytest.mark.skip\|xfail\|pytest.skip" tests/` → no matches. No test is currently disabled, weakened, or known-flaky.

One test's *assertions* were legitimately adjusted mid-project, not weakened:
`tests/social/test_broadcast_queue.py::test_rate_limits_are_independent_per_chat`
was updated in Stage 5 when the global rate cap made its original single-drain
assumption physically impossible (two chats can't both hit 20 messages in one
drain if the global cap is 30/sec) — it now advances the clock between two
drains and still proves the same thing (per-chat independence). Explained in
commit `f2d3160`.

## 6. The exact next step

**Before writing more code:** run Stage 0. Nothing past Phase 0's live-trade
spike has touched a real network, and nothing past that has touched a real
human. Add `@fadepvp_bot` to one real group chat with one real other person,
`/setup`, have them `/join`, trade something small, tap Fade or Copy for
real. This will surface UX and copy issues no amount of additional mocked
testing can find, and it's cheap — a $5–10 round trip, not a redesign.

**If continuing to build code**, in priority order:

1. **Close the loop on what Stage 4 built but never exercised.** Actually
   deploy: rent a small VPS, set up Postgres, run `uv run alembic upgrade
   head` against it, register the webhook per [DEPLOYMENT.md](DEPLOYMENT.md)
   §5, and confirm `/ping` replies through the real webhook path — not just
   the unit tests in `tests/test_webhook.py`. This is the single highest-value
   remaining task: everything else is still running on one laptop in polling
   mode.
2. **Set `FADE_DAILY_VOLUME_CAP_USD` explicitly in the real `.env`.** It's in
   `.env.example` and Stage 5's code defaults to `1000` if absent, but the
   real `.env` file doesn't currently set it — confirm the default is
   actually what's wanted before real users arrive.
3. **Wire a real error reporter.** `src/fade/errors.py`'s `ErrorReporter`
   protocol is a two-line implementation away from Sentry (or anything);
   right now `NullErrorReporter` means crashes are invisible unless someone
   is tailing logs.
4. **Then** pick up [ROADMAP.md](ROADMAP.md) Stage 8 (flex cards) or Stage 9
   (challenges) — correcting the stale "Stage 3" references to "Stage 6"
   first, per §1 above.
5. **F-26 (metrics/daily digest) has no plan at all yet** — if you want
   visibility into users/volume/fees/errors before or during a real launch,
   that needs to be scoped from scratch; it isn't in ROADMAP.md.

Whatever you do, verify state before trusting anything in this file or any
other doc here — this codebase moved fast across many sessions, and the
first rule that's applied throughout is: check the actual file/test/git
state before asserting what it says, don't just trust the last summary.
