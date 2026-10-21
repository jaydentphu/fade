# Fade — Clans v2 Plan

Written 2026-10-21. Status: **Phases A and B built** (decisions at the bottom,
recorded as DECISIONS.md D-15). Phase C (flair) is not built.

## What pvp.trade does (research)

Sources: pvp.trade's command reference and points pages (docs.pvp.trade), plus
third-party guides from 2024–25. Their clan UI lives inside the bot, so the
docs are thin; this is everything they publicly document.

| pvp.trade | Detail |
|---|---|
| Clans are separate from group chats | `/leaderboards` ranks *members of the current group chat* (1d / week / 7d / 30d / all). `/clanleaderboards` ranks *clans* against each other. Two different things. |
| One clan per user | Commands say "your current clan"; `/clan` manages it. |
| Owner powers | `/clankick` removes members, owner only. |
| Join by link | Community posts share `pvp.trade/join/<code>` invite links. |
| Clan leaderboard | Ranked by PnL for the current week. Shows top 5, bottom 5, and your own clan's position. Resets every Saturday. |
| Rewards | The top 10 clans each week get bonus points. A clan needs **≥3 active members** at the snapshot to qualify. |
| Rivalry | `/taunt`: send another clan a message or sticker. |

What we take from it: clans as their own entity (not "a group chat"), one
clan per user, invite links, owner kick, a weekly clan board that always shows
*your* position, a minimum-members rule against one-person "clans", and
taunts later. What we do better: daily, weekly and all-time boards, ranked by
both % and $, plus a clan identity (name, icon, photo).

## The core change

Today a clan **is** a group chat (PRD F-16: `/setup` in a group registers it).
That can't support names, icons, invites or kicks. Telegram owns those for a
group, and a person in five groups is in five "clans" at once.

**Proposal:** split the two concepts.

- **Group chats** keep everything they do today: `/setup`, broadcasts with
  Fade/Copy, the per-group member `/leaderboard`, challenges. Internally the
  current `Clan`/`ClanMember` models are renamed `GroupChat`/`GroupMember`.
  Behaviour is unchanged.
- **Clans** become teams: their own row with a name, icon, owner, members and
  invite code. A user is in at most one clan. A clan can optionally link one
  group chat as its **home chat**, where clan announcements post.

This is a deliberate departure from PRD F-16's wording, so it gets a new
DECISIONS entry (D-15). The PRD itself is not edited.

## Roles and permissions

| Action | Owner | Officer | Member |
|---|---|---|---|
| Invite (link or in-group button) | ✅ | ✅ | ❓ (default ❌) |
| Kick | ✅ anyone | ✅ members only | ❌ |
| Promote / demote officer | ✅ | ❌ | ❌ |
| Rename, set icon/photo/motto | ✅ | ❌ | ❌ |
| Link / unlink home group chat | ✅ | ✅ | ❌ |
| Regenerate invite link (kills the old one) | ✅ | ✅ | ❌ |
| Transfer ownership | ✅ | ❌ | ❌ |
| Disband | ✅ (confirm screen) | ❌ | ❌ |
| Leave | Only after transferring, or when last member (= disband) | ✅ | ✅ |

## Clan identity

- **Name:** 3–24 chars, letters/digits/spaces/`-_.'!`. Unique case-insensitively.
  Latin only, because cards render Latin only (README known limitation).
  Sanitized like every other user string (PRD §7.6). Owner can rename once
  every 7 days.
- **Icon:** one emoji from a curated picker of ~24 (⚔️🐺🦈🔥💀👑🐂🐻🎯🧠🦅🐍 …).
  Shown next to the name on every text leaderboard. Default ⚔️.
- **Photo (optional):** the owner sends a photo in DM. We store Telegram's
  `file_id` only, never the bytes. Shown on the clan profile (`/clan` sent as a
  photo with the info as its caption). A user-uploaded image is only ever shown
  to someone who opens that clan's profile, and admins get `/admin clanreset
  <name>` to wipe a name, photo or motto.
- **Motto (optional):** ≤ 80 chars, shown on the profile.
- **Member cap:** 50.

## Flows

**Create** (DM): `/clan` when not in a clan shows **Create clan** and
**Join with link**. Create asks for the name, then the icon picker, then
optionally a photo. Onboarding is required: you can't create a clan without
an account.

**Invite:**
1. Owners and officers get a link like `t.me/fadepvp_bot?start=clan<code>`,
   with a random 8-char code. It reuses the existing `/start` payload routing
   that `ref<id>` and `withdraw` already use.
2. In a group, an owner or officer replies to someone's message with
   `/clan invite`. The bot posts "⚔️ @x, you've been drafted into
   **Name**" with a **Join** button that only that user can tap.
3. Tapping a link while not onboarded runs onboarding first, then resumes the
   join. The pending code is stashed the same way the referrer id is today.

**Join:** a confirm screen: "Join ⚔️ Name? (You'll leave 🐺 Other.)". The join
fails cleanly if the clan is full, disbanded, the link was regenerated, or you
were kicked less than 24h ago.

**Kick:** `/clan`, then **Members**, then tap a member, then **Kick**, then
**Confirm**. The kicked user gets a plain DM. They can't rejoin that clan for
24h, even with a valid link.

**Leave:** `/clan`, then **Leave**, then **Confirm**. A 24h cooldown applies
before joining *another* clan (anti-hopping, see Scoring).

**Home chat:**
- An owner or officer runs `/setup` in a group. If they own or officer a clan
  and the group has no clan yet, the bot asks "Link this chat as ⚔️ Name's
  home?"
- After linking, weekly results and clan events (joins, kicks, rank changes)
  post there.

## Leaderboards

`/clans` (DM or group) shows one inline-keyboard message that switches between
six views by editing itself:

```
[ Today ] [ This week ] [ All time ]
[ % ROI ] [ $ PnL ]
```

Each view shows the top 10, then your own clan's rank if it isn't in the top 10
(the pvp.trade pattern).

```
🏆 Clans · This week · ROI

1. 🦈 Shark Tank      +41.2%   +$1,204   9 members
2. 🔥 Degens Anon     +18.7%     +$311   14 members
3. 🐺 Wolfpack         +6.0%      +$88   4 members
…
14. ⚔️ Your Clan       −3.1%      −$42   6 members
Resets Sun 6pm ET · min 3 members · min $100 closed
```

### Scoring

- **$ PnL:** the sum of members' realized PnL in the window. It reuses
  `leaderboard.realized_events` and resolutions, with exactly the same math as
  today's board (D-6).
- **% ROI:** clan realized PnL divided by the clan's **cost basis of the
  positions closed** in the window.
  - For a sell, cost = average cost × shares sold. For a resolution, cost =
    payout − pnl.
  - This is capital-weighted, so one member's $2 lottery ticket can't carry
    the clan.
  - It replaces today's league math (PnL ÷ volume). Volume counts buys *and*
    sells, so a clean double reads as +50% instead of +100%.
- **Windows:**
  - Today: since 6pm ET yesterday. Daily, on the same boundary as the weekly
    reset.
  - This week: since Sunday 6pm ET (D-6, unchanged).
  - All time.
- **Eligibility:**
  - A clan needs ≥3 members to appear at all (pvp.trade's rule).
  - The % board also needs ≥$100 of closed cost basis in the window, so
    tiny-sample ROI can't top it.
  - Clans below the bar still see their own line, marked "(not ranked)".
- **Attribution:** a closed trade counts for the clan the user was in **when
  it closed**.
  - Membership is stored as intervals (`joined_at`, `left_at`), not a single
    row, so leaving doesn't erase a clan's history and hopping doesn't import
    PnL from your last clan.
  - The 24h join cooldown blocks open-here-close-there gaming at the edges.
- **Realized only** for ranking, same as the current leaderboard (D-6).
  Unrealized PnL moves with every price tick and would let a clan "win the
  week" on a position it never closes.

### Weekly results (replaces today's F-21 league post)

At Sunday 6pm ET each clan's home chat, if it has one, gets its rank on
**both** boards, its MVP, and the top clan. Clans without a home chat get it
by DM to the owner. The existing `run_daily` league job is reused.

## Data model

| Table | Columns |
|---|---|
| `group_chats` (renamed from `clans`) | unchanged |
| `group_members` (renamed from `clan_members`) | unchanged |
| `clans` (new) | `id`, `name`, `name_key` (lowercase, unique), `icon`, `photo_file_id?`, `motto?`, `owner_id`, `home_chat_id?` (unique), `invite_code` (unique), `renamed_at?`, `created_at`, `disbanded_at?` |
| `clan_memberships` (new) | `id`, `clan_id`, `telegram_user_id`, `role` (owner/officer/member), `joined_at`, `left_at?`, `left_reason?` (left/kicked/disbanded). Partial unique index: one open membership per user. |

**Migration (Alembic, reversible):**
1. Rename the two tables.
2. Create the new ones.
3. **Backfill**: every existing registered group becomes a clan, with name =
   group title (de-duplicated with a suffix), owner = whoever ran `/setup`,
   and home chat = that group.
4. Each user's membership goes to the earliest group they joined, because of
   the one-clan rule.
5. They stay `GroupMember`s of every group, so no one loses any broadcasts.

## Commands

| Command | Where | Purpose |
|---|---|---|
| `/clan` | DM | Clan profile + menu (Invite · Members · Settings · Leave), or Create/Join if clanless |
| `/clan invite` | group, as a reply | Draft a specific user with a Join button |
| `/clans` | anywhere | Clan leaderboards (6 views) |
| `/leaderboard` | group | Unchanged: this group's members |
| `/setup` | group | Unchanged, plus an offer to link as clan home chat |
| `/taunt` | — | Later (Phase C) |

Group messages never show balances or addresses (CLAUDE.md). Clan boards show
PnL and ROI only, the same exposure as today's board. Members with `/privacy`
on are counted in clan totals but never itemized by dollar amount.

## Build phases

- **A. Core (one stage, ~1 session)**
  - Model rename + new tables + backfill migration.
  - A `social/clans.py` service with all rules as pure, tested functions:
    names, roles, cap, cooldowns, attribution.
  - The `/clan` DM menu: create, icon, photo, motto, rename, invite link,
    members, kick, promote, transfer, leave, disband.
  - Deep-link join, `/clans` with the 6 views, and the league switched to clans
    with both rankings.
  - Tests for every permission edge and for the migration on a seeded SQLite DB.
- **B. Group glue**
  - `/clan invite` reply-drafting.
  - Home-chat linking via `/setup`.
  - Clan event posts in the home chat.
- **C. Flair**
  - A clan card image in the flex-card style (icon/photo, name, rank, weekly
    ROI), for `/clan` and the Sunday post.
  - `/taunt`.

Each phase ends with `uv run pytest -q` and `uv run ruff check .`, and with
README + HANDOFF + DECISIONS D-15 updated.

## Decisions (answered 2026-10-21)

1. Clans are separate teams, one per user, with an optional home group chat.
2. Invites: owner and officers only.
3. Icon: emoji picker + optional photo.
4. Ranking: realized (closed) PnL only.

Recorded as DECISIONS.md D-15. Phases A and B are built; C (clan card image,
`/taunt`) is not.
