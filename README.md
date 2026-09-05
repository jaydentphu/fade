![Fade](brand/banners/fade-banner.png)

# Fade

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![python-telegram-bot 21+](https://img.shields.io/badge/python--telegram--bot-21%2B-26A5E4?logo=telegram&logoColor=white)
![Polymarket CLOB on Polygon](https://img.shields.io/badge/Polymarket_CLOB-Polygon-7B3FE4?logo=polygon&logoColor=white)
![tests 252 passing](https://img.shields.io/badge/tests-252%20passing-4c1?logo=pytest&logoColor=white)
![license proprietary](https://img.shields.io/badge/license-proprietary-lightgrey)

A Telegram bot for trading Polymarket prediction markets — solo in DMs, or head-to-head in group chats where every trade can be copied or faded by one tap.

**Status: pre-launch.** The bot is code-complete and covered by 252 passing tests, but it has not been deployed to production and has never handled real user funds. The custody model below is implemented and tested; it has not yet been exercised against live money.

## Why

Trading Polymarket normally means leaving Telegram, opening a browser, connecting a wallet, and clicking through a full order form — for a bet your group chat is already arguing about. Fade collapses that to pasting a link and tapping a button, and adds the layer Polymarket's own UI doesn't have: when someone in your group trades, everyone sees it and can instantly mirror it or take the other side.

## Features

- **Paste-a-link trading** — drop a Polymarket URL in DM, get a market card with live prices, buy YES/NO with preset or custom sizes
- **Copy / Fade** — every trade in a group chat broadcasts as a card with one-tap buttons: Copy mirrors the trade, Fade takes the opposite side
- **Clans and leaderboards** — register a group as a clan, track weekly and all-time PnL, run clan-vs-clan league rankings
- **Head-to-head challenges** — challenge a specific user to a wager on a market; both sides execute automatically when accepted
- **Non-custodial wallets** — each user gets their own on-chain wallet; deposits, withdrawals, and position exports happen directly from the bot
- **Order safety** — every order carries a worst-price slippage guard, a builder-fee attribution code, and an idempotency key so a retried Telegram webhook can never double-execute a trade
- **Admin controls** — a kill switch that pauses new trading without blocking withdrawals, plus a daily digest of volume, fees, and errors

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="assets/market-card-and-broadcast.png" alt="Market card with YES/NO prices and preset sizes, a group broadcast of a member's position with copy and fade buttons, and the reply posted after a fade" width="100%">
      <br>
      <sub><b>Copy / Fade</b> — the market card, the broadcast a member's trade posts to the group, and the reply the bot threads underneath after someone fades it. <i>(Design mockup.)</i></sub>
    </td>
    <td width="50%" valign="top">
      <img src="assets/flex-card.png" alt="Generated card showing a resolved winning position: market question, side, entry and settled price, and profit" width="100%">
      <br>
      <sub><b>Flex card</b> — real output from <code>src/fade/social/flex_card.py</code>, generated and DM'd to a user when one of their positions resolves in profit.</sub>
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
- **Testing/tooling:** pytest + pytest-asyncio, ruff for lint/format, `uv` for dependency management

## Project layout

```
src/fade/
  bot/         Telegram command and callback handlers
  trading/     Market lookup and order execution against the CLOB
  wallets/     Wallet creation, signing isolation, deposit/withdrawal
  social/      Clans, leaderboards, broadcasts, flex cards
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
- This is a solo project built end-to-end by one person, including the security model — it hasn't had an external audit.

## Docs

Further detail lives in `docs/`: `PRD.md` (full product spec), `DEPLOYMENT.md` (production setup), `SECURITY.md` (custody model in depth), and `DECISIONS.md` (a running log of non-obvious design decisions and why they were made).

## License

Copyright (c) 2026 Jayden Phu. All rights reserved — published for portfolio and evaluation purposes, not licensed for reuse. See [LICENSE](LICENSE).
