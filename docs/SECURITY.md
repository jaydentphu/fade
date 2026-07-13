# What Fade can and cannot do with your money

Plain language, no lawyer-speak, per PRD §7.9. If anything here stops being
true, that's a bug — file it loudly.

## Where your money actually lives

When you run /start, Fade creates a wallet that belongs to **you** on the
Polygon blockchain. Your deposits (converted to pUSD, Polymarket's dollar
token) and your market positions sit in that wallet — not in some shared
company account, and never mixed with anyone else's funds, including the
operator's.

## What Fade's backend CAN do

- **Place and cancel orders for you.** That's the product. When you tap Buy or
  Fade, our server signs an order from your wallet and submits it to
  Polymarket. Every order carries a worst-price guard so it can't fill at a
  price meaningfully worse than you saw.
- **Send withdrawals you asked for.** The /withdraw flow moves funds only
  after you type the amount, paste the destination, and tap Confirm in a DM.
  First-time addresses get an extra warning step.
- **See your balance and positions.** Needed to show you /wallet and
  /positions.

## What Fade's backend WILL NEVER do in normal operation

- Move your funds anywhere you didn't explicitly confirm.
- Show your balance, wallet address, or deposit info in a group chat. Group
  cards show your trades (side, market, and — unless you turn on /privacy —
  size), never your wallet.
- Write your keys to logs or the main database. Signing keys live in a
  separate store, encrypted with a key that exists only in the server's
  environment.

## The honest part: what a hacker could do if they fully owned our server

Fade holds an encrypted signing key for your wallet — that's what lets a
Telegram tap become a real trade without you managing a wallet app. If an
attacker fully compromised the server AND the encryption key, they could use
your key the same ways we can: place bad trades with your money, or move
funds. That is the standing risk of every bot-custody model, and we'd rather
say it plainly than pretend otherwise. What limits it:

- Keys are encrypted at rest; the encryption key is not stored beside them.
- The signing code is one small, isolated module — the only code that ever
  touches keys — which keeps the attack surface auditable.
- A kill switch (/pause) stops all trading instantly. It can never stop
  withdrawals — by design, you can always leave.
- Small stakes make small targets: don't deposit more than you'd bet with
  friends.

## Your exit, always

/export hands you the private key to your own wallet after a warning screen.
With it, you control your funds directly from any wallet app — with or
without Fade, forever. You are never trapped.

## Who runs this

One person (the builder), honestly. Target uptime is 99.5%, not five nines.
Fade earns a 1% builder fee on executed trades, paid by Polymarket — there
are no other fees, and no custom fee code exists in this bot.

## Custody modes (PRD §7.8)

Fade has two wallet custody modes; both can coexist.

**Server custody (the classic mode, and today's default).** Everything above
describes it: Fade holds an encrypted signing key in a separate store, can
place orders with it, and `/export` hands it to you whenever you want out.
The residual risk is stated honestly above — a full server compromise
including the encryption key could misuse signing keys.

**Client custody (Mini App, flag-gated rollout).** New wallets are created in
a Telegram Mini App: the key is generated in your device's browser and only
the public address is sent to Fade. The server never sees, stores, or can
recover the key — `/export` for these wallets tells you exactly that. The
trade-off in the current build: server-signed trading and withdrawals are not
yet supported for client-custody wallets (Fade can't sign with a key it
doesn't have); that support arrives with the in-app signing round-trip.
Until then this mode is off by default (`FADE_MINIAPP_CUSTODY`), and the
classic mode keeps working unchanged.
