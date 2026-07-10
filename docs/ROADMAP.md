# Fade — Post-Phase-3 Roadmap

Ordered by importance. Stages 1–2 are load-bearing
infrastructure and safety work — skipping ahead to the fun stuff (Stages 5–6)
before these are done means launching something unreliable and unsafe with
better toys. Each stage has a `/goal` command below its notes, written in the
same style as the Phase 1–3 goals already run in this project: specific
proof requirements, exact commands, and constraints that protect existing
work. Paste the `/goal` block as-is into Claude Code to run that stage.

**Stage 0 (do this yourself, not a coding task):** schedule the 1% taker fee
rate on your builder dashboard now (3-day lag, see docs/DECISIONS.md), and get
one real friend into a real group chat to `/setup` and actually trade. Nothing
past Phase 3 has been exercised by a real human yet — that test will surface
more real bugs than any amount of further building will.

---

## Stage 4 — Production deployment readiness

**Why first:** everything built so far runs on your laptop in polling mode
against a SQLite file. That's fine for solo testing; it is not a real product.
Nobody can use this bot reliably, including you, until it can run unattended,
survive a restart without losing in-flight trades, and be diagnosed when it
breaks. This is the single biggest gap between "phase 3 complete" and
"launchable."

**What it delivers:** a webhook receiver (the stack was always meant to use
FastAPI here, per CLAUDE.md), Postgres compatibility, database-backed card/
picker state (so a redeploy doesn't strand someone mid-trade), and a plug-in
point for error alerting so you find out about crashes before your users do.

```
/goal Production deployment readiness for Fade is complete, proven as follows,
all pasted into the conversation: (1) A FastAPI webhook receiver exists
(src/fade/webhook.py or similar) that verifies the
X-Telegram-Bot-Api-Secret-Token header against TELEGRAM_WEBHOOK_SECRET,
rejects any request with a missing or wrong secret with 401 without touching
the Application, and forwards valid requests into the same
Application.process_update() pipeline polling already uses — a test proves
both the rejection and the successful-forward cases. Polling mode must keep
working unchanged for dev (CLAUDE.md: "webhook mode in prod, polling in
dev"). (2) The app has no SQLite-only code paths blocking a Postgres
DATABASE_URL: `grep -rn "sqlite3\|PRAGMA" src/fade --include=*.py` is shown
and contains nothing outside migration tooling, and the existing test suite
passes unchanged (SQLAlchemy's async engine is already database-agnostic via
DATABASE_URL, so this is a verification pass, not a rewrite). (3) The
in-memory bot_data caches for market cards, position-sell cards, and clan
fade/copy cards move to a DB-backed table with a timestamp and a defined
expiry, so a process restart does not silently strand an in-flight trade — a
test proves a card written before a simulated restart (fresh session/cache)
is still retrievable and a card older than the expiry window is not,
returning the existing "card expired" message rather than a crash. (4) A
pluggable error-reporting interface exists that the global error handler
calls on every unhandled exception (so Sentry or similar can be wired in
later without touching handler code) — a test proves the handler invokes it
with the exception and user id, never with key material. (5) `uv run pytest
-q` exits 0 with the full suite passing and `uv run ruff check .` is clean.
(6) docs/DEPLOYMENT.md exists explaining, for a non-devops solo builder: how
to set the Telegram webhook URL, which env vars are required in prod
(including generating TELEGRAM_WEBHOOK_SECRET), the Postgres connection
string format, and a suggested process manager for keeping the bot running —
contents pasted into the conversation. Constraints: do not weaken or delete
any existing test; do not modify docs/PRD.md or CLAUDE.md; no test may hit a
live network or a live database beyond the existing SQLite test fixtures.
Stop after 45 turns or 4 hours and report status honestly if the condition is
not met.
```

---

## Stage 5 — Safety hardening for real users

**Why second:** this is the direct continuation of the two things you
deliberately waived earlier (D-11's deposit cap, and the daily-volume-ceiling
line from PRD §7.7) — fine to defer while it was just you testing, not fine
once Stage 0's real friend (or anyone beyond them) is trading with real money
and you're not watching every transaction personally. Do this before you
widen the circle of who has access, not after something goes wrong.

**What it delivers:** a per-user daily trade-volume ceiling, a global
(cross-chat) broadcast rate limit to complement the per-chat one already
built, and a basic anomaly alert to you when a user's trading spikes.

```
/goal Safety hardening for real users is complete, proven as follows, all
pasted into the conversation: (1) A per-user daily trade-volume ceiling
(env var FADE_DAILY_VOLUME_CAP_USD, pick and justify a sensible default in
the commit) is enforced in TradingService.execute_market_order BEFORE the
CLOB is touched — a test proves an order that would push a user's rolling
24-hour filled volume over the cap is rejected with a clear plain-language
message and never reaches the CLOB, a test proves an order under the cap is
unaffected, and a test proves the cap resets after 24 hours by advancing a
fake clock (same pattern already used in tests/social/test_leaderboard.py
for the weekly reset). (2) BroadcastQueue gains a global send-rate cap
(~30 messages/second across ALL chats combined, per PRD §6) in addition to
the existing per-chat 20/minute cap — a test proves that with many different
chats each individually under their own per-chat budget, a single drain
cycle never emits more than the global cap, using the same injectable-clock
pattern as tests/social/test_broadcast_queue.py. (3) An anomaly alert DMs
FADE_ADMIN_USER_ID when a single user's trading volume in a rolling window
crosses a defined threshold relative to their own trailing average — a test
proves the alert fires under the trigger condition and does not fire under
normal trading volume, and fires at most once per incident (no spam on every
subsequent order). (4) `uv run pytest -q` exits 0 with the full suite passing
and `uv run ruff check .` is clean. (5) docs/DECISIONS.md gets one new
entry (next available D-number) recording the chosen default cap and
threshold values and the reasoning, in the same style as the existing D-1
through D-11 entries — contents pasted into the conversation. Constraints: do
not weaken or delete any existing test, including the Stage 1 tests if that
stage has already run; do not modify docs/PRD.md or CLAUDE.md; no live
network calls in tests. Stop after 35 turns or 3 hours and report status
honestly if the condition is not met.
```

---

## Stage 6 — Resolution lifecycle (PRD F-14)

**Why third:** this is the last piece of the core trading loop that's
missing. Right now a user's position just sits there after a market
resolves — no notification, no closure, and the leaderboard's realized PnL
silently only counts half of what D-6 defined ("realized on sells AND on
resolutions"). Users won't trust a product that goes quiet at the exact
moment that matters most: finding out if they won.

**What it delivers:** a background job (same shape as the existing deposit
scanner) that detects resolved markets tied to open positions, DMs each
affected user a brand-voice result (PRD: "roast or crown"), and completes
the leaderboard's PnL calculation.

```
/goal Resolution lifecycle (PRD F-14) is complete, proven as follows, all
pasted into the conversation: (1) A background job, following the same
restart-safe polling pattern as src/fade/bot/handlers/deposits.py (persisted
watermark, idempotent on tx/resolution identity so a second poll never
double-notifies), detects when a market tied to a user's held position has
resolved and DMs that user a result message: brand-voice "crown" copy for a
win, brand-voice "roast" copy for a loss (deposit/withdraw/confirmation
screens stay plain per CLAUDE.md brand voice rules, but this is not one of
those — F-14 explicitly wants personality here), including the settlement
PnL. A test proves a resolved winning position triggers exactly one DM with
crown-style copy, a test proves a resolved losing position triggers exactly
one roast-style DM, and a test proves polling the same resolution twice does
not send a second DM. (2) fade.social.leaderboard's realized-PnL calculation
is extended so a position that resolves without ever being manually sold
still contributes to realized_pnl, volume, and win/closed-trade counts,
completing the D-6 definition ("realized on position sells AND on market
resolutions") — a test proves a held-to-resolution winning position appears
in compute_leaderboard's realized PnL and win rate identically in shape to
an existing sell-based test, without changing how sell-based realization
already works. (3) `uv run pytest -q` exits 0 with the full suite passing
and `uv run ruff check .` is clean. Constraints: do not weaken or delete any
existing test, including tests/social/test_leaderboard.py's existing
sell-based assertions, which must keep passing unchanged; do not modify
docs/PRD.md or CLAUDE.md; no test may hit a live network — mock the
resolution-detection data source the same way chain calls are mocked
elsewhere in this codebase. Stop after 40 turns or 4 hours and report status
honestly if the condition is not met.
```

---

## Stage 7 — Clan growth features (PRD F-21, F-22)

**Why fourth:** now that the core loop is solid and safe, these are the
cheapest, highest-leverage retention features — they're pure reads over data
you already collect (`orders`, `clan_members`), no new trading surface, no
new risk. This is where "one clan captain brings 40 people" (the PRD's own
Dev persona thesis) actually starts to compound.

**What it delivers:** a cross-clan weekly league table with a Sunday
announcement, and `/record @user` for bragging-rights lookups.

```
/goal Clan growth features (PRD F-21, F-22) are complete, proven as follows,
all pasted into the conversation: (1) A weekly clan-vs-clan league: a
function that ranks ALL registered clans by aggregate weekly PnL% (reusing
fade.social.leaderboard's existing weekly-window logic from D-6) and a
Sunday announcement path that posts to EACH clan's own chat, naming that
clan's rank out of the total, the top clan overall, and that clan's own MVP
member for the week — a test proves the ranking computation orders clans
correctly by aggregate weekly PnL%, and a test proves the per-clan
announcement text contains that clan's own rank, the leading clan's name, and
an MVP callout. (2) `/record @user` shows: all-time win rate, biggest single
win, biggest single loss, current streak (consecutive wins or losses), and
who they've faded most often — a test proves each of these five stats is
computed correctly from a seeded set of Orders and clan fade/copy history,
matching the realized-PnL logic already established in
fade.social.leaderboard rather than inventing a second calculation path. The
command is DM-only or works in any chat at your discretion, but must never
reveal wallet address or balance regardless of where it's invoked (same
group-safety rule as every other social command). (3) `uv run pytest -q`
exits 0 with the full suite passing and `uv run ruff check .` is clean.
Constraints: do not weaken or delete any existing test; do not modify
docs/PRD.md or CLAUDE.md; no live network calls in tests; reuse
fade.social.leaderboard's realized-PnL and win-rate logic rather than
duplicating it for /record. Stop after 35 turns or 3 hours and report status
honestly if the condition is not met.
```

---

## Stage 8 — Flex cards (PRD F-23)

**Why fifth:** the shareable "Called It" image is a real growth lever (it's
the thing that leaves the group chat and lands on someone's timeline) but it
depends on Stage 3's resolution job existing first, and it's pure upside, not
safety-critical — hence after the growth-but-safe Stage 4, not before it.

**What it delivers:** an auto-generated image summarizing a winning
resolved position, sent to the user for sharing.

```
/goal Shareable flex cards (PRD F-23) are complete, proven as follows, all
pasted into the conversation: (1) When a position resolves in profit (hooking
into the Stage 3 resolution job — if Stage 3 has not yet run, build against
a clearly documented interface for "position resolved in profit" so it wires
in cleanly once Stage 3 lands), an image is generated summarizing: the market
question, the side taken, entry price, exit/resolution price, and dollar and
percent PnL, styled to match Fade's brand voice. A test proves the image
generation function returns valid, non-empty image bytes (decodable as a
real image format) for a given resolved-position input. A test proves the
data placed into the image (market question, PnL figures) matches the input
exactly — since pixel-level text rendering isn't practically unit-testable,
test the data pipeline into the renderer, not the rendered pixels. (2) The
image is sent to the user in DM with a caption inviting them to share it, and
a test proves this DM is sent exactly once per resolved winning position
(idempotent, same restart-safety discipline as the rest of this codebase —
do not regenerate or re-send on a second poll). (3) `uv run pytest -q` exits
0 with the full suite passing and `uv run ruff check .` is clean. Constraints:
do not weaken or delete any existing test; do not modify docs/PRD.md or
CLAUDE.md; no test may hit a live network; if an image-rendering library
needs adding as a dependency, prefer a well-maintained pure-Python one (e.g.
Pillow) and record the addition plainly rather than silently. Stop after 35
turns or 3 hours and report status honestly if the condition is not met.
```

---

## Stage 9 — Head-to-head challenges (PRD F-24)

**Why last:** explicitly scoped in the PRD itself as a "v1.5 candidate," and
it's the most structurally different feature here — a challenge is a
side-wager between two specific users layered on top of an existing market,
not a broadcast of an ordinary trade. It's a genuinely new interaction
pattern, which is exactly why it should land once everything under it is
already solid.

**What it delivers:** `/challenge @user <market>` — propose a head-to-head
bet, opponent accepts or declines, both sides trade, and a settlement note
declares the winner when the market resolves.

```
/goal Head-to-head challenges (PRD F-24) are complete, proven as follows, all
pasted into the conversation: (1) `/challenge @user <market url>` in a group
proposes a wager: the challenger picks a side and size, the opponent is
prompted to accept (auto-taking the opposite side, mirroring the existing
Fade opposite-side logic in fade/bot/handlers/clan.py) or decline — a test
proves proposing creates a pending challenge record naming both users, the
market, and the challenger's side/size, and a test proves an unrelated third
user tapping accept/decline is rejected (same owner-lock pattern already
proven in tests/bot/test_fade_copy.py's NOT_YOUR_PICKER_ALERT case). (2)
Accepting executes both sides' trades through the existing TradingService
(same FOK, slippage guard, builder code, and idempotency guarantees as every
other order path — no new order-execution logic) — a test proves acceptance
places two orders, one per side, both attributed to the correct user. (3)
When the challenged market resolves (hook into the Stage 3 resolution job's
interface, same as Stage 5 — document the interface if Stage 3 hasn't landed
yet), a settlement message posts to the group naming the winner and the
result — a test proves the correct winner is declared for a seeded resolved
outcome on each side, and a test proves this settlement message posts
exactly once (idempotent). (4) Declining or a challenge left unanswered past
a defined timeout cleanly cancels with no funds moved — a test proves this.
(5) `uv run pytest -q` exits 0 with the full suite passing and `uv run ruff
check .` is clean. Constraints: do not weaken or delete any existing test;
do not modify docs/PRD.md or CLAUDE.md; no live network calls in tests; reuse
TradingService.execute_market_order rather than building a parallel
order-placement path. Stop after 45 turns or 4 hours and report status
honestly if the condition is not met.
```
