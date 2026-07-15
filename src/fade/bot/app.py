from __future__ import annotations

import inspect
import logging
from datetime import datetime
from datetime import time as dtime

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from fade.bot.cards import CardStore
from fade.bot.handlers import (
    admin,
    basic,
    challenge,
    clan,
    deposits,
    discover,
    export,
    history,
    invite,
    onboarding,
    positions,
    record,
    resolutions,
    router,
    security,
    slippage,
    trade,
    wallet,
    withdraw,
)
from fade.bot.handlers import digest as digest_handlers
from fade.config import Settings
from fade.db.session import make_engine, make_session_factory
from fade.errors import build_reporter
from fade.social.broadcast import BroadcastQueue
from fade.trading.service import TradingService
from fade.wallets import WalletService

DEPOSIT_SCAN_INTERVAL_S = 30
BROADCAST_DRAIN_INTERVAL_S = 3
# Resolutions settle slowly (hours/days after close): 5 minutes is plenty.
RESOLUTION_SCAN_INTERVAL_S = 300
# Expired UI cards get physically deleted this often (TTL is 24h; hourly is plenty).
CARD_PURGE_INTERVAL_S = 3600

logger = logging.getLogger(__name__)


async def _log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Exceptions from Polymarket/SDK never contain key material (only addresses),
    # so logging the traceback is safe. Update contents are not logged.
    user_id = (
        update.effective_user.id if isinstance(update, Update) and update.effective_user else None
    )
    logger.error("Unhandled error (user=%s)", user_id, exc_info=context.error)
    reporter = context.application.bot_data.get("error_reporter")
    if reporter is not None and context.error is not None:
        # Pluggable sink (Sentry etc. wire in here without touching handlers).
        # Passed the exception and user id only — never update contents or keys.
        maybe = reporter.report(context.error, user_id=user_id)
        if inspect.isawaitable(maybe):
            await maybe


async def _post_init(application: Application) -> None:
    """Register the public command menu with Telegram so "/" autocompletes.
    Built from the same registry as /help and onboarding — one source of truth."""
    from telegram import BotCommand

    from fade.bot.handlers.basic import COMMANDS

    await application.bot.set_my_commands(
        [BotCommand(name, description) for name, description in COMMANDS]
    )


async def _broadcast_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.application.bot_data["broadcasts"].drain_once()


async def _card_purge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hourly cleanup of expired UI cards (Stage 11: the table otherwise grows
    forever — expiry was only ever checked at read time)."""
    removed = await context.application.bot_data["cards"].purge_expired()
    if removed:
        logger.info("purged %d expired ui cards", removed)


async def _league_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs daily at 18:00 ET; only Sunday actually announces (PRD J3, D-6).

    Checking the weekday here instead of trusting scheduler day-indexing keeps
    the behavior explicit and testable.
    """
    from fade.social.leaderboard import EASTERN
    from fade.social.league import announce_league

    now = datetime.now(tz=EASTERN)
    if now.weekday() != 6:  # Sunday
        return
    await announce_league(
        context.application.bot_data["sessions"],
        context.application.bot_data["broadcasts"],
        now,
    )


def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.telegram_bot_token).post_init(_post_init).build()

    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    wallets = WalletService(settings, sessions)
    app.bot_data["settings"] = settings
    app.bot_data["sessions"] = sessions
    app.bot_data["wallets"] = wallets

    async def _admin_alert(text: str) -> None:
        # Anomaly alerts DM the builder (PRD §7.7). No admin configured = no-op.
        if settings.admin_user_id:
            await app.bot.send_message(chat_id=settings.admin_user_id, text=text)

    # Config-assembled sinks (Stage 12): SENTRY_DSN -> Sentry, admin id ->
    # throttled DMs, neither -> the no-op default. Wired first so the
    # background loops below can report caught failures.
    reporter = build_reporter(settings, app.bot.send_message)
    app.bot_data["error_reporter"] = reporter

    async def _deactivate_clan(chat_id: int) -> None:
        # Bot removed from the chat (Forbidden): deactivate, don't delete —
        # leaderboard/record history references this clan's members.
        from fade.db.models import Clan

        async with sessions() as session:
            clan = await session.get(Clan, chat_id)
            if clan is not None and clan.active:
                clan.active = False
                await session.commit()
                logger.warning("clan %s deactivated: bot was removed from the chat", chat_id)

    app.bot_data["trading"] = TradingService(settings, sessions, wallets, alert=_admin_alert)
    app.bot_data["deposit_scanner"] = deposits.DepositScanner(
        deposits.Rpc(settings.polygon_rpc_url), sessions, reporter=reporter
    )
    app.bot_data["resolution_scanner"] = resolutions.ResolutionScanner(
        sessions, fetch=resolutions.fetch_resolved_positions, reporter=reporter
    )
    app.bot_data["broadcasts"] = BroadcastQueue(
        send=app.bot.send_message, on_forbidden=_deactivate_clan, reporter=reporter
    )
    # DB-backed so in-flight trade cards survive a restart (memory is the fast path).
    app.bot_data["cards"] = CardStore(session_factory=sessions)
    # F-26: daily ops digest to the admin (no admin configured = digest off).
    app.bot_data["digest_sender"] = digest_handlers.DigestSender(sessions, settings.admin_user_id)

    # Commands
    app.add_handler(CommandHandler("start", onboarding.start))
    app.add_handler(CommandHandler("wallet", wallet.wallet))
    app.add_handler(CommandHandler("positions", positions.positions))
    app.add_handler(CommandHandler("withdraw", withdraw.withdraw))
    app.add_handler(CommandHandler("export", export.export))
    app.add_handler(CommandHandler("trending", discover.trending))
    app.add_handler(CommandHandler("search", discover.search))
    app.add_handler(CommandHandler("setup", clan.setup))
    app.add_handler(CommandHandler("join", clan.join))
    app.add_handler(CommandHandler("privacy", clan.privacy))
    app.add_handler(CommandHandler("leaderboard", clan.leaderboard))
    app.add_handler(CommandHandler("record", record.record))
    app.add_handler(CommandHandler("challenge", challenge.challenge))
    app.add_handler(CommandHandler("security", security.security))
    app.add_handler(CommandHandler("pause", admin.pause))
    app.add_handler(CommandHandler("resume", admin.resume))
    app.add_handler(CommandHandler("stats", admin.stats))
    app.add_handler(CommandHandler("ping", basic.ping))
    app.add_handler(CommandHandler("help", basic.help_command))
    app.add_handler(CommandHandler("slippage", slippage.slippage))
    app.add_handler(CommandHandler("history", history.history))
    app.add_handler(CommandHandler("invite", invite.invite))

    # Callback queries
    app.add_handler(
        CallbackQueryHandler(onboarding.attest_callback, pattern=f"^{onboarding.ATTEST_CALLBACK}$")
    )
    app.add_handler(CallbackQueryHandler(trade.size_callback, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(trade.confirm_callback, pattern="^tc:"))
    app.add_handler(CallbackQueryHandler(trade.cancel_callback, pattern="^tx$"))
    app.add_handler(CallbackQueryHandler(positions.sell_callback, pattern="^ps:"))
    app.add_handler(CallbackQueryHandler(positions.sell_partial_callback, pattern="^psp:"))
    app.add_handler(CallbackQueryHandler(positions.sell_confirm_callback, pattern="^psc:"))
    app.add_handler(CallbackQueryHandler(slippage.set_callback, pattern="^sl:"))
    app.add_handler(CallbackQueryHandler(withdraw.confirm_callback, pattern="^wd:"))
    app.add_handler(
        CallbackQueryHandler(export.export_callback, pattern=f"^{export.EXPORT_CALLBACK}$")
    )
    app.add_handler(CallbackQueryHandler(discover.view_callback, pattern="^v:"))
    # Clan copy/fade loop
    app.add_handler(CallbackQueryHandler(clan.fade_copy_callback, pattern="^(fd|cp):"))
    app.add_handler(CallbackQueryHandler(clan.size_callback, pattern="^s[fc]:"))
    app.add_handler(CallbackQueryHandler(clan.confirm_callback, pattern="^gc:"))
    app.add_handler(CallbackQueryHandler(clan.cancel_callback, pattern="^gx:"))
    # Head-to-head challenges (F-24)
    app.add_handler(CallbackQueryHandler(challenge.pick_callback, pattern="^hp:"))
    app.add_handler(CallbackQueryHandler(challenge.accept_callback, pattern="^ha:"))
    app.add_handler(CallbackQueryHandler(challenge.decline_callback, pattern="^hd:"))
    app.add_handler(CallbackQueryHandler(challenge.cancel_callback, pattern="^hx:"))
    app.add_handler(CallbackQueryHandler(challenge.withdraw_callback, pattern="^hc:"))
    # Category browsing (F-9)
    app.add_handler(CallbackQueryHandler(discover.category_callback, pattern="^c:"))

    # Free text in DMs: pasted URLs and flow replies
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, router.handle_text
        )
    )

    app.add_error_handler(_log_error)

    if app.job_queue is not None:
        app.job_queue.run_repeating(_card_purge_job, interval=CARD_PURGE_INTERVAL_S, first=120)
        app.job_queue.run_repeating(deposits.scan_job, interval=DEPOSIT_SCAN_INTERVAL_S, first=10)
        app.job_queue.run_repeating(
            _broadcast_job, interval=BROADCAST_DRAIN_INTERVAL_S, first=BROADCAST_DRAIN_INTERVAL_S
        )
        app.job_queue.run_repeating(
            resolutions.scan_job, interval=RESOLUTION_SCAN_INTERVAL_S, first=60
        )
        from fade.social.leaderboard import EASTERN

        app.job_queue.run_daily(_league_job, time=dtime(hour=18, minute=0, tzinfo=EASTERN))
        # F-26 daily digest: morning ET, same run_daily pattern as the league.
        app.job_queue.run_daily(
            digest_handlers.digest_job, time=dtime(hour=9, minute=0, tzinfo=EASTERN)
        )
    return app
