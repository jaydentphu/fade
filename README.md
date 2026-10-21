![Fade](brand/banners/fade-banner.png)

# Fade

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![python-telegram-bot 21+](https://img.shields.io/badge/python--telegram--bot-21%2B-26A5E4?logo=telegram&logoColor=white)
![Polymarket CLOB on Polygon](https://img.shields.io/badge/Polymarket_CLOB-Polygon-7B3FE4?logo=polygon&logoColor=white)
![tests 368 passing](https://img.shields.io/badge/tests-368%20passing-4c1?logo=pytest&logoColor=white)
![license proprietary](https://img.shields.io/badge/license-proprietary-lightgrey)

A Telegram bot for trading Polymarket prediction markets — solo in DMs, or head-to-head in group chats where every trade can be copied or faded by one tap.

## Why

Trading Polymarket normally means leaving Telegram, opening a browser, connecting a wallet, and clicking through a full order form — for a bet your group chat is already arguing about. Fade collapses that to pasting a link and tapping a button, and adds the layer Polymarket's own UI doesn't have: when someone in your group trades, everyone sees it and can instantly mirror it or take the other side.

## Features

- **Paste-a-link trading** — drop a Polymarket URL in DM, get a market card with live prices, buy YES/NO with preset or custom sizes
- **Copy / Fade** — every trade in a group chat broadcasts as a card with one-tap buttons: Copy mirrors the trade, Fade takes the opposite side
- **Clans** — form a team with a name, icon, and photo; invite friends by link, promote officers, kick members; clans compete on daily, weekly, and all-time boards ranked by both % ROI and $ PnL, with weekly results posted to the clan's home group chat
- **Group leaderboards** — every group chat ranks its own members by realized PnL, volume, and win rate, weekly and all-time
- **Head-to-head challenges** — challenge a specific user to a wager on a market; both sides execute automatically when accepted
- **Flex cards** — every settled position gets a shareable PnL card (green "Called it" for wins, red "Got faded" for losses), and a Flex button in `/positions` cards any open position mid-trade; each carries a QR code to the sharer's referral link
- **Non-custodial wallets** — each user gets their own on-chain wallet; deposits, withdrawals, and position exports happen directly from the bot
- **Order safety** — every order carries a worst-price slippage guard, a builder-fee attribution code, and an idempotency key so a retried Telegram webhook can never double-execute a trade
- **Admin controls** — a kill switch that pauses new trading without blocking withdrawals, plus a daily digest of volume, fees, and errors

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="assets/trade-cards.png" alt="Two generated trade cards: a market card showing a 61% chance of YES with a YES/NO probability bar and 61 and 39 cent prices, and a group broadcast card showing priya buying YES at 61 cents for $50 against an open NO slot" width="100%">
      <br>
      <sub><b>Copy / Fade</b> — real output from <code>src/fade/social/trade_card.py</code>: the market card you get when you paste a link, and the card a member's buy posts to their clan. Fade and Copy buttons sit under it; Fade takes the open slot.</sub>
    </td>
    <td width="50%" valign="top">
      <img src="assets/flex-cards.png" alt="Two generated flex cards: a green 'Called it' card for a winning position at +63.9%, and a red 'Got faded' card for a losing position at -100%, each with invested amount, profit or loss, entry and settled price, and a referral QR code" width="100%">
      <br>
      <sub><b>Flex cards</b> — real output from <code>src/fade/social/flex_card.py</code>, DM'd to a user when one of their positions settles, win or lose. The QR opens the bot on the sharer's referral link.</sub>
    </td>
  </tr>
</table>

## Architecture: how custody works

The core design constraint: **a full compromise of the application server should not be enough to steal user funds.**

- Every user has their own on-chain wallet on Polygon (a Polymarket-managed deposit wallet, not a shared pool). Funds and positions live there, not in a house account.
- The application server never touches raw private keys. Signing key material is encrypted at rest in a physically separate datastore from the main application database, and exactly one small module in the codebase is permitted to read it. Every other module gets a scoped, authenticated client for a single action — never the key itself.
- Withdrawals require an explicit confirmation in DM, and first-time destination addresses go through a cooldown with an immediate security notification, so a hijacked session can't silently drain funds the moment a new address appears.
- Users can export their own key at any time — custody is never a lock-in.
- An optional (flag-gated) alternate mode moves key generation into a Telegram Mini App running in the user's own browser, so the server never sees the key at all. This mode is a working slice, not the default: it currently supports wallet creation but not yet server-signed trading, and is off unless explicitly enabled.

Every order is FOK (fill-or-kill) with a slippage guard computed from the live order book, executes through Polymarket's Builders Program for fee-attributed trades, and is recorded with a client-generated idempotency key *before* it touches the CLOB — so a Telegram retry of the same update can't place a duplicate order.

## Tech stack

- **Language / runtime:** Python 3.11+, asyncio throughout
- **Bot layer:** [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) (v21+), long polling in dev / FastAPI webhook receiver in production
- **Trading:** Polymarket's official `polymarket-client` SDK against the CLOB, via the Builders Program for fee attribution
- **Chain access:** web3.py / eth-account for Polygon reads and signing, plain JSON-RPC for deposit detection
- **Data:** SQLAlchemy 2.x + Alembic migrations; SQLite for dev, Postgres in production
- **Images:** Pillow + qrcode for the trade and flex cards, set in the Geist typeface (OFL, vendored)
- **Testing/tooling:** pytest + pytest-asyncio, ruff for lint/format, `uv` for dependency management

## Project layout

```
src/fade/
  bot/         Telegram command and callback handlers
  trading/     Market lookup and order execution against the CLOB
  wallets/     Wallet creation, signing isolation, deposit/withdrawal
  social/      Clans, leaderboards, broadcasts, trade and flex cards
  db/          SQLAlchemy models + Alembic migrations
  miniapp.py   Optional client-side-custody Mini App (flag-gated)
  webhook.py   Production FastAPI webhook receiver
```

## Setup

Requires Python 3.11+ and [`uv`](https://github.com/astral-sh/uv).

```bash
uv sync
cp .env.example .env
```

Fill in `.env`: a Telegram bot token from [@BotFather](https://t.me/BotFather), a Polymarket builder code and Builder API key from the [builder dashboard](https://polymarket.com/settings?tab=builder), and generate a signing key:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Run the database migrations, then start the bot in polling mode:

```bash
uv run alembic upgrade head
uv run fade-bot
```

Run the tests:

```bash
uv run pytest -q
uv run ruff check .
```

Production deployment (webhook mode, Postgres, process manager) is documented in `docs/DEPLOYMENT.md`.

## Known limitations

- The client-side-custody Mini App mode covers wallet creation but not yet server-signed trading or withdrawals for wallets created that way — that's flagged off by default and still in progress.
- No limit orders yet — market orders (FOK) only.
- No automatic "always copy this person" rule; Copy/Fade is a one-tap, per-trade action.
- The broadcast queue that posts group trade cards is in-memory, so a process restart drops whatever hadn't been sent yet.
- Flex cards render Latin text only: emoji and non-Latin characters (e.g. Chinese) are dropped from market titles rather than drawn as blank boxes.
- This is a solo project built end-to-end by one person, including the security model — it hasn't had an external audit.

## Docs

Further detail lives in `docs/`: `PRD.md` (full product spec), `DEPLOYMENT.md` (production setup), `SECURITY.md` (custody model in depth), and `DECISIONS.md` (a running log of non-obvious design decisions and why they were made).

## License

Copyright (c) 2026 Jayden Phu. All rights reserved — published for portfolio and evaluation purposes, not licensed for reuse. See [LICENSE](LICENSE).
