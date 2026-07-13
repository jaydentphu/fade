# Deploying Fade (for a solo builder, no devops background)

Two ways to run the bot:

- **Dev (your laptop):** long polling. `uv run fade-bot`. No public URL, no
  webhook secret needed. This is what you've been using.
- **Prod (a server, 24/7):** a FastAPI webhook receiver. Telegram pushes
  updates to a public HTTPS URL you register. This doc covers that.

You don't need Docker or Kubernetes. A cheap Linux VPS ($5–10/mo) plus a
managed Postgres is plenty for the PRD's ~1K-user target.

---

## 1. What prod needs that dev didn't

| Thing | Why |
|---|---|
| A public HTTPS URL | Telegram only sends webhooks to HTTPS |
| `TELEGRAM_WEBHOOK_SECRET` | So only Telegram (not random internet traffic) can post updates |
| Postgres (not SQLite) | SQLite is a single file — fine for one process on your laptop, not for a real server you redeploy |
| A process manager | So the bot restarts itself if it crashes or the server reboots |

---

## 2. Environment variables (prod)

Everything in `.env.example`, with these mattering specifically for prod:

```
TELEGRAM_BOT_TOKEN=...            # from @BotFather
TELEGRAM_WEBHOOK_SECRET=...       # generate below — REQUIRED in webhook mode
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:5432/fade
SIGNING_DATABASE_URL=...          # keep this a SEPARATE database from DATABASE_URL
FADE_SIGNING_KEY=...              # the Fernet key; if you lose it, all user keys are unrecoverable
FADE_ADMIN_USER_ID=...            # your numeric Telegram id (kill switch auth)
FADE_BUILDER_CODE=...             # public, from the builder dashboard
FADE_BUILDER_API_KEY=...          # + SECRET + PASSPHRASE, from the builder dashboard
RELAYER_API_KEY=... / _ADDRESS=...
POLYGON_RPC_URL=...               # use a keyed provider (Alchemy/Infura) in prod, not a public one
FADE_WITHDRAWAL_COOLDOWN_MINUTES=60  # see below — 0 (off) is a dev default, not a prod one
```

**Withdrawal cooldown (`FADE_WITHDRAWAL_COOLDOWN_MINUTES`, recommend `60` in
prod):** first-time withdrawal destinations wait this many minutes between
being registered and being able to receive funds, and registering one
immediately DMs the account owner (PRD §7.3, docs/DECISIONS.md D-13). This is
the difference between a hijacked Telegram session draining a wallet silently
in seconds versus the real owner getting a notice and a one-hour window to
`/export` and move funds first. 60 minutes is long enough to read a
notification and act, short enough that a legitimate user setting up a new
exchange deposit address isn't meaningfully blocked (they confirm, get a
"ready at HH:MM" time, and come back). The default is `0` (off) only so dev
and the test suite behave exactly as before this feature existed — set it
explicitly in prod.

Generate the webhook secret:

```
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Back up `FADE_SIGNING_KEY` somewhere safe and offline.** It decrypts every
user's signing key. Lose it and every user must re-onboard; leak it and the
encryption at rest is worthless. It is the single most sensitive value here.

---

## 3. Database setup

The schema is managed by Alembic and is already Postgres-compatible — no code
changes. On the server, with the prod `DATABASE_URL` set:

```
uv run alembic upgrade head
```

This creates all tables in Postgres. Run it again after any future migration.
The signing store (`SIGNING_DATABASE_URL`) can be a second Postgres database or
its own SQLite file on an encrypted volume — just keep it physically separate
from the main database, per the security model.

---

## 4. Running the webhook server

The webhook app is served by uvicorn (an ASGI server):

```
uv run uvicorn "fade.webhook:create_webhook_app" --factory --host 0.0.0.0 --port 8080
```

- `--factory` because `create_webhook_app()` is a function that builds the app.
- It exposes `POST /telegram/webhook` (the receiver) and `GET /health` (a
  liveness check you can point uptime monitoring at).
- Put this behind a reverse proxy that terminates HTTPS (Caddy is the simplest
  — it gets you a free auto-renewing certificate in about three lines of
  config; nginx + certbot also works).

---

## 5. Telling Telegram where to send updates

Once your server answers HTTPS at, say, `https://bot.yourdomain.com`, register
the webhook ONCE (replace the token and secret):

```
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://bot.yourdomain.com/telegram/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

Telegram will now include that secret in the `X-Telegram-Bot-Api-Secret-Token`
header on every delivery; the receiver rejects anything without the exact match
(401). To confirm it took: `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`.

To go back to polling for local debugging, delete the webhook first
(`.../deleteWebhook`) — a bot can't do both at once.

---

## 6. Keeping it running

Simplest option on a Linux VPS is a systemd service. Create
`/etc/systemd/system/fade.service`:

```
[Unit]
Description=Fade bot (webhook)
After=network.target

[Service]
WorkingDirectory=/home/YOU/Fade
ExecStart=/home/YOU/.local/bin/uv run uvicorn "fade.webhook:create_webhook_app" --factory --host 0.0.0.0 --port 8080
EnvironmentFile=/home/YOU/Fade/.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now fade`. It now starts on boot and restarts on
crash. Logs: `journalctl -u fade -f`.

---

## 7. Monitoring

Everything here is config-driven — nothing below is required for the bot to
run, but run prod blind at your own peril.

**Error reporting.** The app calls an error reporter on every unhandled
exception AND every caught background-loop failure. It receives only the
exception and the affected user id — never message contents or key material —
so it's safe to send to a third-party service. Two sinks assemble themselves
from env vars (either, both, or neither):

- `SENTRY_DSN=https://...@sentry.io/...` — sends errors to Sentry. Needs the
  optional dependency: `uv sync --extra sentry` (declared as the `sentry`
  extra in pyproject.toml).
- `FADE_ADMIN_USER_ID` set — unhandled errors are also DM'd to you directly,
  throttled to one DM per error signature per hour, so a crash-loop produces
  one alert instead of a flooded chat.

With neither set, the reporter is a no-op (the dev default).

**Daily digest (F-26).** With `FADE_ADMIN_USER_ID` set, the bot DMs you once
a day (09:00 ET) with the last 24 hours: new users, active users (placed an
order), filled order count and USD volume, builder fees earned, failed or
rejected order count, deposit and withdrawal counts, and whether trading is
paused. `/stats` (admin-only, DM-only) returns the same digest on demand.
Aggregates only — it never contains a user's wallet address or balance.

**What to check weekly:** the digest's failed/rejected count trending up
(fill quality or SDK trouble), builder fees vs. volume (fee attribution
still working), withdrawal count sanity (an unexpected spike is worth a
look at `journalctl -u fade`), and your Sentry inbox if configured.

---

## 8. Pre-launch checklist

- [ ] `uv run alembic upgrade head` against prod Postgres succeeds
- [ ] `TELEGRAM_WEBHOOK_SECRET` set and registered via setWebhook
- [ ] `FADE_SIGNING_KEY` backed up offline
- [ ] `getWebhookInfo` shows your URL with no `last_error_message`
- [ ] `GET /health` returns 200 through your HTTPS domain
- [ ] Send `/ping` in Telegram — bot replies
- [ ] systemd (or equivalent) restarts the bot after a `kill`
- [ ] Builder taker fee actually set to 1% on the dashboard (see docs/DECISIONS.md)
