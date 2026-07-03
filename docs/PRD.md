# PRD: Fade

**Telegram-native social trading for Polymarket. Fight club for forecasters.**

| | |
|---|---|
| Author | Jayden |
| Status | Draft v1.0 |
| Date | July 3, 2026 |
| Target MVP launch | Q4 2026 (solo build, see Timeline) |

---

## 1. Overview

Fade is a Telegram bot that lets people trade Polymarket prediction markets entirely from chat, solo in DMs or competitively in group chats. Users deposit crypto into their own per-account wallet, trade YES/NO markets with simple commands, and, when the bot is added to a group, every member's trade is broadcast with two inline buttons: **Copy** and **Fade**. One tap mirrors the trade or takes the opposite side.

The core insight: prediction markets are natively PvP. Every market resolves to a definitive winner and loser, which makes them better suited to social, competitive trading than perps or spot. Fade productizes the thing friend groups already do informally ("bet you $50 the Fed doesn't cut") with real markets, real settlement, and a permanent public record of who actually calls things right.

**Why now.** Polycule, the previous leading Polymarket Telegram bot (~27k monthly users), was hacked in January 2026 for ~$230K because it stored user private keys on a central server, and it went offline. The market gap is open, and trust has shifted toward products with credible key handling. Meanwhile pvp.trade proved the social/clan model works on Hyperliquid (50k+ monthly users at peak). Nobody has combined the two on Polymarket. Polymarket's Builders Program (builder codes, Relayer, fee attribution) now handles most of the infrastructure a solo builder would otherwise have to build and secure alone.

**Value proposition by audience:**
- For users: the fastest way to trade Polymarket on mobile, and the only way to trade it *with* your friends.
- For the builder: revenue via Polymarket builder-code fees on every attributed trade, computed and paid out by Polymarket natively. No custom fee-skimming logic.

## 2. Goals and Objectives

### Product goals
1. Make placing a Polymarket trade from a phone faster than the official interface: paste a link or tap a button, confirm, done.
2. Make group trading the default behavior, not a bolt-on. The copy/fade broadcast loop is the product.
3. Be credibly safer than Polycule. Funds in user-owned Safes, trade-scoped signing, exportable keys, withdrawal confirmations.

### Business goals (first 6 months post-launch)
| Objective | Target |
|---|---|
| Registered users (started bot + created wallet) | 500 |
| Funded users (deposited ≥ $10) | 150 |
| Active groups/clans with ≥ 3 funded members | 20 |
| Monthly trade volume | $100K |
| Builder fee revenue | Break-even on infra costs (~$50–100/mo) |
| Security incidents involving user funds | 0 |

These are deliberately modest. This is a solo passion project; the goal of v1 is proving the copy/fade loop retains friend groups, not hypergrowth.

## 3. Scope

### In scope (v1)
- Telegram bot (DM + group modes) via Bot API webhooks
- Per-user wallet creation (Polygon Safe holding funds, backend-held order-signing credentials)
- Deposits (USDC on Polygon; bridged deposit from Solana/other chains as fast-follow)
- Withdrawals to a user-confirmed external address
- Solo trading: market search, paste-a-Polymarket-URL to trade, market buy/sell, positions, PnL
- Group trade broadcasting with inline **Copy** / **Fade** buttons
- Group leaderboards (PnL, volume, win rate) with weekly reset + all-time
- Clans: a group chat registered as a clan, clan vs. clan weekly PnL competition
- Builder-code fee attribution (1% per executed trade)
- Onboarding gate: age (18+) and jurisdiction attestation, Polymarket ToS agreement

### Out of scope (v1), planned later
- Limit orders and order management (v1 is market orders only, keeps UX and code simple)
- Automatic copy trading (standing "mirror this wallet" rules); v1 copy is one-tap, per-trade only
- Wallet tracking/whale alerts
- Charts and market analytics (per target user: "not for looking at charts, just for making trades")
- Telegram Mini App for client-side key custody (phase 2 hardening)
- Points program / token (explicitly deferred; Polymarket requires builders to disclose token plans, and a token adds regulatory weight this project doesn't need)
- Fiat on-ramp
- Head-to-head custom challenges ("I bet you $50 that...") built on top of existing markets, v1.5 candidate

### Explicitly not doing
- Storing raw private keys in the application database (see §7, Security)

## 4. Target Audience and Personas

**Primary: The Group Chat Degen ("Marcus", 22)**
Has a 6-person Telegram group that already argues about elections, sports, and crypto. Has maybe used Polymarket's website twice. Won't download another app, but will tap a button under his friend's trade to fade him for $20. Cares about bragging rights more than returns. Success = he checks the leaderboard unprompted.

**Secondary: The Mobile-First Polymarket Trader ("Priya", 28)**
Already trades Polymarket weekly, finds the mobile web flow clunky. Wants: paste link → trade in under 10 seconds, positions at a glance, fast withdrawals. She's the liquidity and volume; Marcus is the growth loop. She'll tolerate the social layer if execution is fast, and will churn instantly on a security scare.

**Tertiary: The Clan Captain ("Dev", 25)**
Runs a 40-person trading group. Wants tools to keep the chat alive: leaderboards, clan wars, weekly recaps. He's the distribution channel; one Dev brings 40 Marcuses.

## 5. Functional Requirements

Priority: **P0** = MVP blocker, **P1** = fast follow (first 1–2 months post-launch), **P2** = later.

### 5.1 Onboarding & Wallets
| ID | Requirement | Priority |
|---|---|---|
| F-1 | `/start` in DM creates account: generates a Polygon Safe for funds + order-signing credentials, after user passes an attestation gate (18+, not in a prohibited jurisdiction, accepts Polymarket ToS and Fade terms) | P0 |
| F-2 | `/wallet` shows Safe address (QR + copyable), USDC balance, and open-position value | P0 |
| F-3 | Deposit = send USDC (Polygon) to Safe address; bot detects deposit and confirms in DM | P0 |
| F-4 | `/withdraw` sends USDC to an external address; first-time addresses require an explicit confirm step; withdrawal always available regardless of bot state | P0 |
| F-5 | `/export` lets the user export their key material after a warning flow, so users always have unilateral access to funds | P0 |
| F-6 | Cross-chain deposit (e.g., USDC from Solana via bridge integration) | P1 |
| F-7 | Wallet actions are DM-only; wallet commands invoked in a group get redirected to DM | P0 |

### 5.2 Trading (DM and group)
| ID | Requirement | Priority |
|---|---|---|
| F-8 | Paste any Polymarket URL → bot returns market card (question, YES/NO prices, resolution date) with Buy YES / Buy NO buttons and preset sizes ($10/$25/$50/custom) | P0 |
| F-9 | `/trending` and `/search <term>` for market discovery, browsable by category | P0 |
| F-10 | Market orders (FOK) with a slippage guard (worst-price limit) on every order | P0 |
| F-11 | Every order carries the Fade builder code for fee attribution | P0 |
| F-12 | `/positions` shows open positions with live PnL and one-tap Sell buttons | P0 |
| F-13 | Trade confirmations show fill price, size, fee, and new balance | P0 |
| F-14 | Resolution notifications: when a user's market resolves, DM them the result (and roast or crown them, per brand voice) | P1 |
| F-15 | Limit orders | P2 |

### 5.3 Social layer (the product)
| ID | Requirement | Priority |
|---|---|---|
| F-16 | Bot added to a group + `/setup` registers the group as a **clan** | P0 |
| F-17 | When a clan member trades, the bot broadcasts a trade card to the group: who, market, side, size (with a per-user privacy toggle to hide dollar amounts and show only side + market), entry price | P0 |
| F-18 | Trade cards carry inline **⚔️ Fade** and **📋 Copy** buttons. Tap → size picker → confirm → executed against the same market. Copy = same side, Fade = opposite side | P0 |
| F-19 | Fade/copy executions post a reply to the original card ("@marcus faded @priya for $25"), building a visible rivalry thread | P0 |
| F-20 | `/leaderboard` per clan: weekly and all-time, ranked by realized PnL, with volume and win-rate columns | P0 |
| F-21 | Clan vs. clan: weekly league table across all registered clans by aggregate PnL %, results announced in each clan every Sunday | P1 |
| F-22 | `/record @user` shows a member's all-time record: win rate, biggest win, biggest loss, current streak, who they've faded most | P1 |
| F-23 | Shareable "Called It" flex card (image) when a position resolves in profit | P1 |
| F-24 | Head-to-head challenge flow (`/challenge @user <market>`) | P2 |

### 5.4 Admin & ops
| ID | Requirement | Priority |
|---|---|---|
| F-25 | Kill switch: builder can pause all trading instantly (withdrawals stay live) | P0 |
| F-26 | Basic metrics dashboard or daily digest: users, volume, fees, errors | P1 |

## 6. Non-Functional Requirements

### Performance
- Trade command → order submitted to CLOB in < 3s p95; confirmation message < 5s p95.
- Trade broadcast to group within 5s of fill.
- Respect Telegram Bot API limits (~30 msg/s global, ~20 msg/min per group). Broadcast queue with per-chat throttling; batch clan-war announcements.

### Reliability
- Webhook-based (not polling) bot architecture with idempotent update handling; Telegram retries must not double-execute trades.
- Every order has a client-generated idempotency key. A dropped connection can never place a duplicate order.
- Withdrawals must work even if trading is degraded or paused.
- Target 99.5% uptime. It's a side project; be honest about it in the docs and status channel.

### Scalability
- v1 sized for ~1K users / ~50 clans on a single small VPS + managed Postgres. Everything stateless except DB and the signing service, so scaling is vertical first, then split services.

### Security (see §7)

### Usability
- Every flow completable one-handed on a phone with taps, not typed parameters, wherever possible. Commands exist for power users; buttons are the default path.
- Brand voice everywhere: confident, funny, a little mean. The bot talks like a cornerman, not a bank. But money screens (deposit, withdraw, confirm) drop the jokes and get plain and precise.

## 7. Security Model

This section exists because the direct predecessor died from exactly this.

**Threat model headline:** Polycule stored user private keys on its server; one breach = total loss ($230K, Jan 2026). Fade's design goal is that a full compromise of the application server cannot drain user funds to an attacker's address.

1. **Funds custody:** each user's USDC and positions live in their own Polygon Safe, not a pooled hot wallet.
2. **Signing separation:** order-signing credentials live in a separate signing service (or managed key infra such as Turnkey, evaluated in Open Questions), encrypted at rest, never in the app database, never logged. The app server requests signatures over vetted payloads; it never touches raw keys.
3. **Trade-scoped power:** the backend's standing capability is limited to placing/canceling orders. Withdrawals require a user-confirmed destination address captured in DM, with a confirm tap, and (P1) a cooldown + notification when the destination changes.
4. **User exit:** keys/credentials are exportable (F-5). Users are never trapped.
5. **Bot token hygiene:** the Telegram bot token is a credential; store it like one. Verify webhook secret token on every update.
6. **Injection surface:** market titles, usernames, and group names are untrusted input; sanitize before rendering and never let them alter command parsing.
7. **Honest residual risk, stated publicly:** a fully compromised backend could still make *bad trades* with user funds (buy junk positions). This is the same residual risk pvp.trade's agent-wallet model carries. Mitigations: per-user daily trade-volume ceilings, anomaly alerts, kill switch (F-25).
8. **Phase 2 hardening:** move key generation client-side via a Telegram Mini App so credentials are provisioned in the user's own web view, closing most of the server-trust gap without leaving Telegram.
9. **Disclosure:** a plain-language `/security` command explaining exactly what Fade can and cannot do with your funds. Post-Polycule, this is a marketing feature, not just documentation.

## 8. Business Model

- **1% builder fee per executed trade**, attributed via Polymarket builder codes and paid out by Polymarket to the builder wallet. No custom fee logic in v1.
- No subscription, no deposit/withdrawal fees. Gas handled via Polymarket's Relayer (gasless UX).
- Positioning vs. market: PolyCop charges 0.5%; Polymarket's own interface has charged ~2% on some flows. 1% is defensible for the social layer; revisit after launch data.
- Upside: Polymarket's weekly USDC rewards program for builders, based on integration volume.
- No token, no points, in v1 (see Scope).

## 9. User Journeys

**J1: First deposit to first trade (Priya, DM)**
1. Taps a friend's referral link → `/start` → attestation gate → wallet created (~10s).
2. `/wallet` → copies Safe address → sends $100 USDC from her exchange → bot DMs "Deposit confirmed: $100."
3. Pastes a Polymarket URL from her browser → market card → taps **Buy YES $50** → confirm → "Filled at 61¢. You now hold 82 YES shares. Fee: $0.50."
Total time from link to position: under 5 minutes, most of it waiting on the deposit.

**J2: The fade (Marcus, group)**
1. Dev adds @FadeBot to the group chat, runs `/setup`, clan "Degens Anonymous" is born.
2. Priya buys $50 YES on "Fed cuts rates in September." Card posts to the group: her position, entry at 61¢, ⚔️ **Fade** / 📋 **Copy** underneath.
3. Marcus, who thinks she's wrong, taps **Fade** → picks $25 → confirm (he onboarded last week). Bot replies to the card: "⚔️ @marcus faded @priya. $25 NO at 39¢. This will be remembered."
4. Market resolves NO in October. Bot posts the receipt: Marcus +$39, Priya −$50, leaderboard updated, Marcus's flex card generated.

**J3: Sunday clan war (Dev)**
1. Sunday 6pm: bot posts to every clan: weekly clan-war results, "Degens Anonymous" ranked #4 of 23 clans, top clan member and clan MVP called out.
2. Dev screenshots it, trash talk ensues, three members who went quiet that week open `/trending`.

**J4: Getting money out (any user)**
1. `/withdraw` in DM → enter amount → paste destination address → bot shows address checksummed with "First time sending here. Confirm?" → tap confirm → USDC arrives → receipt with tx hash.

## 10. Compliance & Legal Posture

Not legal advice; this section records the product stance and flags what needs real counsel if the project becomes a business.

- **Framing:** Fade markets itself as a mobile/social interface for Polymarket, never as a way around geo-restrictions. All public copy avoids "trade from anywhere restricted" language.
- **Attestation gate (F-1):** users must attest they are 18+ and not located in a prohibited jurisdiction, and accept Polymarket's ToS. This mirrors the approach of prior bots and Polymarket's own requirements. It is a fig leaf, not a shield; acknowledged.
- **Escalation triggers:** if the project starts generating real revenue or user counts, before scaling: consult a lawyer on operator liability, consider entity formation (LLC), and review whether geo-blocking (e.g., by Telegram-reported locale or IP at Mini App stage) is warranted.
- **Taxes:** builder fee income is income; track it from day one.

## 11. Success Metrics

**North star: weekly faded/copied trades per active clan.** If the one-tap PvP loop isn't happening, Fade is just another trading bot.

Supporting metrics:
- Activation: % of `/start` users who fund within 7 days (target 30%)
- Social density: % of trades executed via Copy/Fade buttons vs. solo commands (target 25%+)
- Retention: 4-week retention of funded users (target 40%)
- Clan health: clans with ≥ 5 trades/week (target 60% of registered clans)
- Volume & revenue: monthly attributed volume, builder fees earned
- Trust: withdrawal success rate 100%, security incidents 0, median withdrawal time

## 12. Timeline

Solo build, part-time alongside school and internship. Phases, not dates promised to anyone.

| Phase | Duration | Milestone |
|---|---|---|
| 0. Spike | 2–3 wks | Builder code registered; place one real attributed trade on Polymarket CLOB from a script; deposit/withdraw round-trip on a test Safe |
| 1. Solo core | 4–6 wks | DM bot: onboarding, wallet, deposit/withdraw, URL-to-trade, positions. **Private alpha: just me** |
| 2. Social core | 4–6 wks | Clans, broadcasts, Copy/Fade buttons, leaderboard. **Closed beta: my group chats, small deposit caps ($100/user)** |
| 3. Hardening | 3–4 wks | Signing service separation, idempotency audit, kill switch, rate-limit queue, `/security` page, self-audit against §7. Raise caps gradually |
| 4. Launch | — | Public launch: Polymarket builders channel, X/Twitter, prediction-market Telegram groups. Clan wars live |
| 5. Post-launch | ongoing | Resolution roasts, flex cards, clan wars polish, cross-chain deposits, `/record`, then limit orders and auto-copy |

Realistic target: public launch Q4 2026.

## 13. Open Questions & Assumptions

**Open questions**
1. **Key infrastructure:** self-managed encrypted signing service vs. managed infra (e.g., Turnkey). Decision criteria: free-tier limits, latency, and how cleanly it supports a later Mini App migration. Decide during Phase 0.
2. **Exact CLOB integration details:** confirm current V2 SDK order flow, Safe deployment via Relayer, and builder-code registration requirements against live docs during the spike (docs.polymarket.com/builders).
3. **Broadcast privacy defaults:** show dollar amounts by default (more fun, pvp.trade-style) or hide by default (safer socially)? Beta feedback decides.
4. **Fee rate:** 1% is the launch number; validate against fill quality and user feedback.
5. **Name collisions:** check @fade-adjacent Telegram handles, X handle, and domain before public launch; "Fade" is common crypto slang so expect squatting.
6. **Whether clan wars need anti-gaming rules** (e.g., wash-trading between clan members to farm volume) before or after launch.

**Assumptions**
- Polymarket's Builders Program remains open and permissionless for this use case, and builder fees pay out as documented.
- Telegram remains a viable distribution platform for trading bots (no policy change banning them).
- Target users already hold crypto on some chain; fiat onboarding is not required for the first 500 users.
- The builder (Jayden) operates this as a sole developer; no security guarantees beyond §7 are promised to users, and messaging stays honest about that.
