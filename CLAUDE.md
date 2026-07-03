# Fade

Telegram bot for social trading on Polymarket. Full spec: docs/PRD.md. Read it before any feature work.

## Stack (do not substitute without asking)
- Python 3.11+, managed with uv
- python-telegram-bot (v21+, async) for the bot layer, webhook mode in prod, polling in dev
- py-clob-client for Polymarket CLOB, web3.py for chain reads
- SQLAlchemy 2.x + Alembic; SQLite for dev/tests, Postgres in prod
- pytest + pytest-asyncio; ruff for lint/format
- FastAPI only as the webhook receiver, no other web surface

## Architecture rules
- src/fade/ package layout: bot/ (handlers), trading/ (CLOB), wallets/ (Safe + signing), social/ (clans, leaderboards, broadcasts), db/ (models, migrations)
- The signing module is the ONLY code that touches key material. No other module imports it except through its public interface.
- Every order gets a client-generated idempotency key (PRD §6). Telegram webhook retries must never double-execute a trade. There is a test proving this.
- Every order attaches the builder code from env var FADE_BUILDER_CODE and a worst-price slippage guard (PRD F-10, F-11).
- Wallet commands are DM-only (PRD F-7). Group messages never contain addresses or balances.

## Security rules (non-negotiable, from PRD §7)
- Never write private keys, mnemonics, or signing credentials to logs, the database, error messages, or test fixtures. Tests use obviously fake keys.
- Secrets come from env vars only. .env is gitignored. If you find a secret in the repo, stop and flag it.
- Sanitize market titles, usernames, and group names before rendering (PRD §7.6).
- Withdrawals must remain functional even when trading is paused (PRD F-25).

## Testing rules
- All Telegram interactions tested with mocked Update/Context objects. All CLOB calls tested against a mock client. No test hits a live network.
- Do not weaken, skip, or delete an existing test to make the suite pass. If a test is wrong, say so explicitly and fix the test with justification in the commit message.
- Run `uv run pytest -q` and `uv run ruff check .` before declaring anything done, and paste the output.

## Brand voice
- Bot copy is confident, funny, a little mean (fight club for forecasters). EXCEPT deposit, withdraw, and confirmation screens, which are plain and precise. See PRD §6 Usability.