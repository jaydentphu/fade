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
    onboarding,
    positions,
    record,
    resolutions,
    router,
    security,
    trade,
    wallet,
    withdraw,
)
from fade.config import Settings
from fade.db.session import make_engine, make_session_factory
from fade.errors import NullErrorReporter
from fade.social.broadcast import BroadcastQueue
from fade.trading.service import TradingService
from fade.wallets import WalletService

DEPOSIT_SCAN_INTERVAL_S = 30
BROADCAST_DRAIN_INTERVAL_S = 3
# Resolutions settle slowly (hours/days after close): 5 minutes is plenty.
RESOLUTION_SCAN_INTERVAL_S = 300

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


async def _broadcast_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.application.bot_data["broadcasts"].drain_once()


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
    app = Application.builder().token(settings.telegram_bot_token).build()

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

    app.bot_data["trading"] = TradingService(settings, sessions, wallets, alert=_admin_alert)
    app.bot_data["deposit_scanner"] = deposits.DepositScanner(
        deposits.Rpc(settings.polygon_rpc_url), sessions
    )
    app.bot_data["resolution_scanner"] = resolutions.ResolutionScanner(
        sessions, fetch=resolutions.fetch_resolved_positions
    )
    app.bot_data["broadcasts"] = BroadcastQueue(send=app.bot.send_message)
    # DB-backed so in-flight trade cards survive a restart (memory is the fast path).
    app.bot_data["cards"] = CardStore(session_factory=sessions)
    # Default no-op; a prod build can replace with a Sentry-backed reporter.
    app.bot_data["error_reporter"] = NullErrorReporter()

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
    app.add_handler(CommandHandler("ping", basic.ping))

    # Callback queries
    app.add_handler(
        CallbackQueryHandler(onboarding.attest_callback, pattern=f"^{onboarding.ATTEST_CALLBACK}$")
    )
    app.add_handler(CallbackQueryHandler(trade.size_callback, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(trade.confirm_callback, pattern="^tc:"))
    app.add_handler(CallbackQueryHandler(trade.cancel_callback, pattern="^tx$"))
    app.add_handler(CallbackQueryHandler(positions.sell_callback, pattern="^ps:"))
    app.add_handler(CallbackQueryHandler(positions.sell_confirm_callback, pattern="^psc:"))
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

    # Free text in DMs: pasted URLs and flow replies
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, router.handle_text
        )
    )

    app.add_error_handler(_log_error)

    if app.job_queue is not None:
        app.job_queue.run_repeating(deposits.scan_job, interval=DEPOSIT_SCAN_INTERVAL_S, first=10)
        app.job_queue.run_repeating(
            _broadcast_job, interval=BROADCAST_DRAIN_INTERVAL_S, first=BROADCAST_DRAIN_INTERVAL_S
        )
        app.job_queue.run_repeating(
            resolutions.scan_job, interval=RESOLUTION_SCAN_INTERVAL_S, first=60
        )
        from fade.social.leaderboard import EASTERN

        app.job_queue.run_daily(_league_job, time=dtime(hour=18, minute=0, tzinfo=EASTERN))
    return app
