from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from fade.bot.handlers import (
    admin,
    basic,
    clan,
    deposits,
    discover,
    export,
    onboarding,
    positions,
    router,
    trade,
    wallet,
    withdraw,
)
from fade.config import Settings
from fade.db.session import make_engine, make_session_factory
from fade.social.broadcast import BroadcastQueue
from fade.trading.service import TradingService
from fade.wallets import WalletService

DEPOSIT_SCAN_INTERVAL_S = 30
BROADCAST_DRAIN_INTERVAL_S = 3

logger = logging.getLogger(__name__)


async def _log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Exceptions from Polymarket/SDK never contain key material (only addresses),
    # so logging the traceback is safe. Update contents are not logged.
    user_id = (
        update.effective_user.id if isinstance(update, Update) and update.effective_user else None
    )
    logger.error("Unhandled error (user=%s)", user_id, exc_info=context.error)


async def _broadcast_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.application.bot_data["broadcasts"].drain_once()


def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    wallets = WalletService(settings, sessions)
    app.bot_data["settings"] = settings
    app.bot_data["sessions"] = sessions
    app.bot_data["wallets"] = wallets
    app.bot_data["trading"] = TradingService(settings, sessions, wallets)
    app.bot_data["deposit_scanner"] = deposits.DepositScanner(
        deposits.Rpc(settings.polygon_rpc_url), sessions
    )
    app.bot_data["broadcasts"] = BroadcastQueue(send=app.bot.send_message)

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
    return app
