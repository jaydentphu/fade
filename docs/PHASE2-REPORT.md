# Phase 2 Report — PRD F-16 through F-20 (social core)

Date: 2026-07-07. Suite: 94 tests passing (29 new since Phase 1's 65), ruff
clean, no live network calls in tests, zero Phase 1 tests modified.

| F# | Requirement | Status |
|----|-------------|--------|
| F-16 | Bot added to a group + `/setup` registers the group as a clan | **Implemented.** `/setup` (group-only, requires onboarding) creates the `Clan` row and enrolls the registrar. Re-running is idempotent. A `/join` command was added beyond the PRD as the mechanical way members enlist — Telegram doesn't push group membership lists, so membership is observed from activity (/setup, /join, and Fade/Copy taps all enroll). |
| F-17 | Member's trade broadcasts a card to the group: who, market, side, size (with per-user privacy toggle), entry price | **Implemented.** DM buy fills broadcast to every clan the trader belongs to; sells broadcast too (marked "sold", no buttons). `/privacy` toggles `User.hide_amounts`: hidden mode drops the dollar figure but keeps side + market (tested). Broadcasts show dollar amounts by default per D-2. |
| F-18 | Cards carry ⚔️ Fade / 📋 Copy → size picker → confirm → executed on the same market. Copy = same side, Fade = opposite | **Implemented.** Size picker ($10/$25/$50) is owner-locked (another user tapping it gets an alert, tested); confirm executes through the same `TradingService` as DM trades, so FOK, slippage guard, builder code, and update-id idempotency all apply unchanged. Fade buys the opposite token, Copy the same token (both tested). Non-onboarded tappers get a "create an account" alert per D-5. |
| F-19 | Executions post a reply to the original card naming both users | **Implemented.** "⚔️ marcus faded priya for $25 at 0.61. This will be remembered." — posted as a Telegram reply to the original broadcast message (message-id captured when the card is sent) and also replaces the picker message. The actor's own privacy setting hides their size in the reply. |
| F-20 | `/leaderboard` per clan: weekly and all-time, realized PnL, volume, win-rate columns | **Implemented.** Realized PnL: each SELL realizes against the running average buy cost per token (partial sells handled, tested). Win rate is per closed trade (D-6); volume is filled notional. Weekly window starts Sunday 18:00 US Eastern (D-6), covered by a clock-advancing test proving trades drop off the weekly board after the boundary while all-time persists. Top 10 shown. |

## Rate limiting (PRD §6 Performance)

`BroadcastQueue`: per-chat FIFO drained by a 3s job, at most 20 messages per
minute per group (Telegram's per-group limit). Injectable clock + sender;
tested: 30 queued → exactly 20 leave in the window, the rest flow after it
expires; chats are limited independently; order is preserved.

## Group-safety (PRD F-7)

A negative test walks the entire group surface produced by a real fade flow
(card, picker, confirm, rivalry reply, plus everything the queue sent) and
asserts none of it contains "balance", "wallet", or any `0x` address.

## Phase 1 test constraint

**No existing Phase 1 test was modified.** The broadcast hook in
`trade.confirm_callback` / `positions.sell_confirm_callback` no-ops when no
broadcast queue is configured, so Phase 1 handler tests run unchanged.

## Known limitations (recorded, not faked)

1. Fade/Copy executions do not additionally broadcast their own card — the
   F-19 reply on the original card is the notification (avoids echo spam).
   The fill still counts toward the leaderboard like any order.
2. Card/picker state is in-memory (consistent with Phase 1); a restart expires
   open cards with a "trade again" message.
3. Resolution-based realized PnL (market resolves rather than user sells)
   lands with F-14 resolution notifications (P1, post-launch phase) — today's
   PnL realizes on sells only, the D-6 "both" definition's other half.
4. Leaderboard usernames come from our `users` table (set at attestation and
   trade time); users with no Telegram @username display as their numeric id.
