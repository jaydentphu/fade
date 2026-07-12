# Fade — Roadmap Part 3 (Post-Audit: Stages 10–15)

Written 2026-07-12, after Stages 4–9 completed (all PRD F-numbers except F-26
are now built). This roadmap comes from a full-codebase audit for security and
user-experience gaps: every handler, service, and background job was read and
checked against PRD §7, CLAUDE.md's rules, and how it fails when Telegram, the
RPC, or a user misbehaves.

Ordered by importance, same discipline as ROADMAP.md: early stages protect
money and reliability; later stages are polish and architecture. Each stage
has a `/goal` block written for Claude (Fable 5) in the established style —
specific proof requirements, exact commands, constraints that protect existing
work. Paste the `/goal` block as-is to run that stage.

**Standing reminder (still not a coding task):** ROADMAP.md's Stage 0 — a real
second human using the real bot — has still never happened. Every stage below
is cheaper to validate after one real group chat session than before it.

## Audit summary (what's wrong, in one table)

| # | Finding | Severity | Stage |
|---|---|---|---|
| 1 | PRD §7.3's P1 withdrawal hardening (cooldown + notification on new destination) never built | Security | 10 |
| 2 | Withdrawal addresses never EIP-55 checksummed (PRD J4 promises "address checksummed") | Security/UX | 10 |
| 3 | `/export` key message sits in the chat forever; no auto-delete | Security/UX | 10 |
| 4 | Trade/position/discovery callback cards have no owner binding (any group member can tap another's confirm flow; executes as tapper, so no fund theft — but hygiene) | Security | 10 |
| 5 | One user's failed DM (blocked bot) aborts the whole deposit-confirm loop, resolution scan, or broadcast drain for everyone | Reliability | 11 |
| 6 | Bot kicked from a group = Clan rows stay forever; broadcasts fail eternally with no cleanup | Reliability | 11 |
| 7 | `UiCard` key collision (32-bit keys) raises unhandled `IntegrityError`; expired cards are never deleted (table grows unbounded) | Reliability | 11 |
| 8 | Webhook returns 500 on malformed JSON body (should reject 400 without touching the Application) | Hardening | 11 |
| 9 | `NullErrorReporter` is still the default — production crashes are invisible | Ops | 12 |
| 10 | F-26 (metrics/daily digest) — the only unbuilt PRD requirement | Ops | 12 |
| 11 | `/wallet` omits open-position value (F-2 gap, documented since Phase 1) | UX | 13 |
| 12 | Fee line shows only builder fee, not Polymarket's platform fee (F-13 gap) | UX | 13 |
| 13 | No `/help`; Telegram command menu (`set_my_commands`) never registered; onboarding command list stale (missing /record, /challenge, /security, /privacy, /leaderboard) | UX | 13 |
| 14 | `/start` deep-link payloads ignored (group redirect buttons land on a generic overview instead of the flow the user asked for) | UX | 13 |
| 15 | Usernames captured once at attestation and never refreshed — leaderboards/challenges rot as people rename | UX/Data | 13 |
| 16 | Challenges can't target users without a username; a username change strands a pending challenge | UX | 14 |
| 17 | F-9 "browsable by category" still narrowed to trending+search | UX | 14 |
| 18 | Positions/resolutions fetch only the first API page — heavy traders silently truncated | Correctness | 14 |
| 19 | Server-side key custody is the accepted residual risk; PRD §7.8's Mini App migration is the designed fix | Architecture | 15 |

---

## Stage 10 — Withdrawal-path security completion

**Why first:** this is the only stage where the gap is between the product and
its own security promises. PRD §7.3 explicitly lists "a cooldown +
notification when the destination changes" as P1, PRD J4 promises the
confirmation screen shows the address "checksummed," and the predecessor
product died from exactly one thing: the withdrawal path being weaker than
advertised. Everything here protects the moment money leaves.

**What it delivers:** a cooldown window on first-time withdrawal addresses
with an immediate DM notification (so a hijacked session can't drain funds
silently and instantly), EIP-55 checksum display and validation, auto-deletion
of the exported private key message, and owner-binding on interactive trade
cards.

```
/goal Withdrawal-path security completion is complete, proven as follows, all
pasted into the conversation: (1) First-time withdrawal destinations get a
cooldown: when /withdraw is confirmed to an address the user has never used
before, the withdrawal executes only if the address was first registered at
least FADE_WITHDRAWAL_COOLDOWN_MINUTES ago (env var, default 0 = off so dev
and existing tests keep working; pick and justify a sensible production
default in docs/DEPLOYMENT.md). When a first-time address is registered and
the cooldown is active, the user is told plainly when they can complete the
withdrawal and receives an immediate DM notification that a new withdrawal
address was added ("if this wasn't you, use /export now and move your funds"
in plain money-screen language, per PRD §7.3's notification requirement).
A test proves a first-time address inside the cooldown window does not move
funds and sends the notification; a test proves the same address after the
window works; a test proves a previously-used address is unaffected by the
cooldown; a test proves cooldown 0 preserves today's exact behavior.
Withdrawals must remain independent of the trading pause flag (PRD F-25) —
do not couple them. (2) Withdrawal addresses are EIP-55 checksummed
everywhere they are rendered (confirm screen, receipt), using
eth_utils/eth_account already in the dependency tree — a test proves a
lowercase-entered address renders checksummed; mixed-case input that fails
EIP-55 validation is rejected with a plain re-entry prompt and a test proves
it. (3) The /export key message auto-deletes after a defined interval
(suggest 5 minutes, via the job queue) and the warning copy tells the user it
will — a test proves a delete job is scheduled for the key message when the
key is revealed. If the delete fails (user already deleted it), it must not
crash or retry forever. (4) Interactive card callbacks that execute or stage
a trade for the tapping user (trade.py size/confirm, positions.py
sell/sell-confirm, discover.py view) verify the card was issued to that user
where the card is user-specific: position cards record their owner's
telegram_user_id at creation and reject other tappers with the existing
owner-lock alert pattern; shared discovery/market cards in groups remain
tappable by anyone (that's the product) but a test proves a position sell
card created for user A, tapped by user B, is rejected and executes nothing.
(5) `uv run pytest -q` exits 0 with the full suite passing and `uv run ruff
check .` is clean. (6) docs/DECISIONS.md gains one new D-number entry
recording the chosen cooldown default and the reasoning, in the existing
style. Constraints: do not weaken or delete any existing test; do not modify
docs/PRD.md or CLAUDE.md; no live network calls in tests; never log or store
key material; money screens stay plain per brand-voice rules. Stop after 40
turns or 4 hours and report status honestly if the condition is not met.
```

---

## Stage 11 — Background-job resilience and data hygiene

**Why second:** every background loop currently has a single point of failure:
one user who blocked the bot aborts deposit confirmations for everyone behind
them in the loop; one dead group chat jams the broadcast queue; the resolution
scan dies mid-pass the same way. These are the bugs that will actually fire in
week one of real usage (someone WILL block the bot), and each failure is
invisible (see Stage 12) and unfair (it punishes other users). Plus two known
latent bugs: the 32-bit UiCard key collision, and the ui_cards table growing
forever.

**What it delivers:** per-item error isolation in every background loop,
automatic cleanup when the bot is removed from a group, card-store collision
handling and expiry purging, and a webhook that rejects malformed bodies
instead of 500ing.

```
/goal Background-job resilience and data hygiene is complete, proven as
follows, all pasted into the conversation: (1) Per-item error isolation: in
the deposit-confirm loop (deposits.py _confirm_pending), the resolution scan
(resolutions.py — both the crown/roast DM and the flex-card send), and the
broadcast drain (broadcast.py drain_once), a failure sending to ONE
user/chat is caught, logged, and reported through the error-reporter
interface, and the loop continues to the remaining items — a test per loop
proves that with one recipient raising on send, the other recipients still
receive their messages. For deposit confirms specifically, a send failure
must not mark that deposit confirmed (it retries next scan) and a test
proves it; for broadcasts, define and implement a bounded policy (drop after
N failed attempts, not infinite retry) and a test proves a poisoned message
does not block the rest of its chat's queue forever. (2) Group removal
cleanup: when a send to a clan chat fails with Telegram's Forbidden error
(bot kicked), the clan is deactivated or removed so it is skipped by future
broadcasts, league announcements, and challenge settlements — a test proves
a Forbidden-raising chat stops receiving queued sends after cleanup while
other clans are unaffected. Choose deactivation (a flag) over row deletion
if preserving leaderboard history matters, and say which you chose and why.
(3) UiCard hygiene: put() retries with a fresh key on primary-key collision
(bounded retries, then raises) — a test proves a forced collision (seeded
token generator) still stores the card under a different key; and expired
cards are actually deleted from the table by a periodic purge wired into the
existing job queue — a test proves cards older than the TTL are removed and
fresh ones are kept. (4) The webhook rejects a request whose body is not
valid JSON, or whose JSON is not a plausible Telegram update, with a 400
without touching Application.process_update — a test proves both, and the
existing 401 and happy-path tests keep passing unchanged. (5) `uv run pytest
-q` exits 0 with the full suite passing and `uv run ruff check .` is clean.
Constraints: do not weaken or delete any existing test; do not modify
docs/PRD.md or CLAUDE.md; no live network calls in tests; error handling must
never swallow exceptions silently — every caught failure goes to the logger
and the error-reporter interface. Stop after 40 turns or 4 hours and report
status honestly if the condition is not met.
```

---

## Stage 12 — Ops visibility: real error reporting and F-26 metrics

**Why third:** the only PRD requirement not yet built (F-26), plus the
HANDOFF.md warning that has been true since Stage 4: `NullErrorReporter`
means production crashes are invisible unless someone is tailing logs. Stages
10–11 make failures rarer and contained; this stage makes the remaining ones
visible. Do this before real users arrive, because "the bot was down for two
days and nobody knew" is how trust dies quietly.

**What it delivers:** a wired (not just pluggable) error reporter with an
env-configured backend, and F-26's daily digest to the admin: users, volume,
fees, errors.

```
/goal Ops visibility (error reporting + PRD F-26 metrics digest) is complete,
proven as follows, all pasted into the conversation: (1) A concrete error
reporter ships and wires itself from configuration: if SENTRY_DSN is set,
errors go to Sentry (add the sdk as an optional dependency group, recorded
plainly); independent of Sentry, if FADE_ADMIN_USER_ID is set, unhandled
errors are also throttled-DM'd to the admin (at most one DM per defined
window per error signature so a crash-loop cannot flood Telegram) — a test
proves an unhandled handler exception reaches the reporter with the exception
and user id and never any Update contents or key material (extend the
existing tests/test_error_reporter.py contract), and a test proves the
admin-DM throttle (two identical errors inside the window produce one DM).
NullErrorReporter stays the default when nothing is configured, and dev
polling mode keeps working with zero new required env vars. (2) F-26 daily
digest: a job (same job-queue pattern as the league job) DMs
FADE_ADMIN_USER_ID once per day with: new users (24h), active users (placed
an order, 24h), filled order count and USD volume (24h), builder fees earned
(24h, from recorded fills), failed/rejected order count (24h), deposits and
withdrawals count (24h), and current pause status — a test proves the digest
numbers are computed correctly from a seeded day of Orders/Deposits/Users
and a test proves the digest sends exactly once per day boundary using the
injectable-clock pattern already used in tests/social. An /stats admin-only
command returns the same digest on demand (reusing the same computation, not
a second one) — a test proves non-admins are refused with the existing
NOT_ADMIN_TEXT pattern and admins get the digest. Digest and /stats are
DM-to-admin only and must never render any user's wallet address or
balance. (3) `uv run pytest -q` exits 0 with the full suite passing and `uv
run ruff check .` is clean. (4) docs/DEPLOYMENT.md gains a short "Monitoring"
section: how to set SENTRY_DSN, what the digest contains, and what to check
weekly — pasted into the conversation. Constraints: do not weaken or delete
any existing test; do not modify docs/PRD.md or CLAUDE.md; no live network
calls in tests (mock the Sentry transport if the sdk is exercised at all);
digest copy is ops-plain, not brand-voice. Stop after 40 turns or 4 hours
and report status honestly if the condition is not met.
```

---

## Stage 13 — UX completion of already-shipped features

**Why fourth:** these are the known "done, but incomplete" edges of features
users already touch every session — each one is small, none is risky, and
together they remove the most common moments of confusion: a wallet screen
that understates your worth, a fee line that doesn't match your fill, a
command the bot never told you exists, and a redirect button that forgets why
you tapped it.

**What it delivers:** F-2's open-position value in /wallet, F-13's complete
fee itemization, /help + Telegram's native command menu + a current command
list, /start deep-link routing, and username freshness.

```
/goal UX completion of shipped features is complete, proven as follows, all
pasted into the conversation: (1) /wallet shows open-position value (PRD F-2,
the documented Phase-1 gap): total current value of open positions (reusing
TradingService.list_positions — no new data path) displayed alongside the
pUSD balance, with a graceful plain-text fallback if the positions fetch
fails (balance still shows; no crash) — a test proves the position value
renders from mocked positions and a test proves the fetch-failure fallback.
(2) Fee itemization (PRD F-13): the fill confirmation itemizes BOTH the
builder fee and Polymarket's platform fee (price-curve formula documented in
docs/SPIKE-NOTES.md §4; compute from the SDK the same way the builder fee is
fetched, with an honest "estimated" label if exactness isn't available from
the fill response) — a test proves both fee lines render from a mocked fill
and that the new-balance figure is unchanged (it was always correct).
(3) Discoverability: a /help command renders the command list; the list is
CURRENT (includes /record, /challenge, /security, /privacy, /leaderboard,
and everything in onboarding's existing COMMANDS_TEXT — refactor to one
shared constant so onboarding and /help can never drift apart); and the bot
registers its commands with Telegram via set_my_commands at application
startup so the "/" menu autocompletes — a test proves /help contains every
registered command name, and a test proves the startup path calls
set_my_commands with the same set. (4) /start deep-link payloads route: the
group-redirect buttons already send t.me/bot?start=withdraw|positions|start
payloads — an onboarded user arriving with payload "withdraw" enters the
withdraw flow, "positions" gets their positions, unknown payloads fall back
to today's overview — a test per payload proves the routing, and
non-onboarded users still get the attestation gate first regardless of
payload. (5) Username freshness: on every command and callback interaction
by an onboarded user, if their current Telegram username differs from
users.username, it is updated (cheap, in the existing session work — no new
queries per message beyond what handlers already do; an update-on-change is
acceptable) — a test proves a renamed user's leaderboard/record output shows
the new name after their next interaction. (6) `uv run pytest -q` exits 0
with the full suite passing and `uv run ruff check .` is clean. Constraints:
do not weaken or delete any existing test; do not modify docs/PRD.md or
CLAUDE.md; no live network calls in tests; money screens stay plain;
group-safety rules (no balances/addresses in groups) apply to every new
render path, including /help in a group. Stop after 40 turns or 4 hours and
report status honestly if the condition is not met.
```

---

## Stage 14 — Social and discovery polish

**Why fifth:** growth-loop friction removal. Real but not urgent: the
challenge flow's username dependency will annoy the first group that hits it,
F-9's category browsing is the PRD's last narrowed scope note, and first-page
API truncation will eventually make a heavy trader's /positions silently
wrong. None of these lose money; all of them lose polish.

**What it delivers:** challenges that survive username quirks (and can be
cancelled by the challenger), category browsing for F-9 as written, and
pagination for positions/resolutions.

```
/goal Social and discovery polish is complete, proven as follows, all pasted
into the conversation: (1) Challenge robustness: /challenge accepts a
text_mention entity (users without a username) by capturing the mentioned
user's real id at propose time — for such challenges the accept/decline
owner-lock checks the stored opponent_id instead of username, and a test
proves a no-username opponent can be challenged and accept; username-based
challenges keep working unchanged (existing tests untouched). The challenger
can cancel their own pending (unaccepted) challenge from the card — atomic
pending->cancelled, opponent-lock inverted (only the challenger may cancel),
no funds moved — a test proves cancellation and proves a third user cannot
cancel. A pending challenge whose opponent renamed their username can still
be resolved: document the behavior you implement (at minimum, the challenger
cancel path plus TTL expiry already guarantees no challenge is stuck
forever). (2) Category browsing (PRD F-9 as written): /trending (or a new
/categories entry point — your call, justify it) offers category buttons
(sourced from the market data the SDK already returns — tags/categories on
markets; mock it in tests) that list top markets within that category using
the existing discovery card flow — a test proves tapping a category renders
that category's markets through the same cache/keyboard path as /search
results (no parallel card system). (3) Pagination: TradingService
list_positions and resolutions.fetch_resolved_positions iterate all pages of
the SDK's paged responses instead of first_page() only — a test with a
mocked multi-page response proves items from page 2 appear in results for
both. Bound the iteration (a sane max-pages constant) so a pathological
account cannot spin the scanner forever, and say what bound you chose.
(4) `uv run pytest -q` exits 0 with the full suite passing and `uv run ruff
check .` is clean. Constraints: do not weaken or delete any existing test —
tests/bot/test_challenge.py's existing username-flow assertions must keep
passing unchanged; do not modify docs/PRD.md or CLAUDE.md; no live network
calls in tests; reuse the existing card store and TradingService paths
rather than building parallel ones. Stop after 45 turns or 4 hours and
report status honestly if the condition is not met.
```

---

## Stage 15 — Mini App client-side key custody (PRD §7.8)

**Why last:** the biggest security upgrade in the PRD, and the biggest lift.
Everything before it is incremental; this changes the trust model — keys
generated in the user's own Telegram Mini App web view, so a fully
compromised server can no longer export user keys at all. It's last not
because it matters least but because it's the only stage that needs product
decisions (migration of existing users, recovery UX) that deserve real-user
feedback from Stages 0–14 first. Treat the /goal below as the engineering
slice: the new-user path behind a flag, coexisting with the current model.

**What it delivers:** a Telegram Mini App page that generates the signing key
client-side and registers only the public material with the backend; new
wallets created through it never have their private key touch the server.
Existing users keep working unchanged; migration UX is deliberately out of
scope for this slice.

```
/goal Mini App client-side key custody (PRD §7.8, engineering slice) is
complete, proven as follows, all pasted into the conversation: (1) A Mini App
page served by the existing FastAPI webhook app (the stack's only web
surface, per CLAUDE.md — no new server) that: validates Telegram's
initData signature server-side per the official algorithm (HMAC of the bot
token, checked before ANY state change; a test proves a tampered initData is
rejected 401 and a valid one accepted, using a fake bot token — this is
pure crypto, no network), generates the signing key in the browser
(client-side JS, key never in a request body — the page submits only the
derived public address/credentials needed to register), and registers the
user's wallet against their authenticated Telegram id. (2) A feature flag
(FADE_MINIAPP_CUSTODY, default off) switches /start's wallet-creation step
for NEW users to the Mini App flow (inline button opening the web app);
default-off keeps every existing test and the current flow byte-identical —
a test proves flag-off behavior is unchanged and a test proves flag-on
onboarding hands out the Mini App button instead of creating a server-side
signer. (3) Server-side capability honesty: for a Mini App wallet, /export
explains the server does not hold the key (plain language, this is the
selling point) — a test proves the branch; withdrawals and trading for
Mini App wallets are either implemented through the same signing interface
abstraction (SigningStore's documented seam: "the same interface can later
be backed by managed infra") or explicitly return a clear "not yet
supported in this slice" message with the flag off by default — state
plainly in the conversation which of the two you shipped and why, and prove
whichever it is with a test. (4) SECURITY.md (or /security's text) gains a
section explaining the two custody modes truthfully, including that the old
mode still exists — pasted into the conversation. (5) `uv run pytest -q`
exits 0 with the full suite passing and `uv run ruff check .` is clean.
Constraints: do not weaken or delete any existing test; do not modify
docs/PRD.md or CLAUDE.md; no live network calls in tests; key material must
never appear in logs, the database, request bodies, or test fixtures beyond
obviously-fake constants; the flag defaults OFF and nothing about the
default path may change while it is off. Stop after 50 turns or 5 hours and
report status honestly if the condition is not met.
```
