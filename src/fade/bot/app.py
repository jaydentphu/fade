from __future__ import annotations

from telegram.ext import Application, CallbackQueryHandler, CommandHandler

from fade.bot.handlers import basic, deposits, onboarding, wallet
from fade.config import Settings
from fade.db.session import make_engine, make_session_factory
from fade.wallets import WalletService

DEPOSIT_SCAN_INTERVAL_S = 30


def build_application(settings: Settings) -> Application:
    app = Application.builder().token(settings.telegram_bot_token).build()

    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    app.bot_data["settings"] = settings
    app.bot_data["sessions"] = sessions
    app.bot_data["wallets"] = WalletService(settings, sessions)
    app.bot_data["deposit_scanner"] = deposits.DepositScanner(
        deposits.Rpc(settings.polygon_rpc_url), sessions
    )

    app.add_handler(CommandHandler("start", onboarding.start))
    app.add_handler(CommandHandler("wallet", wallet.wallet))
    app.add_handler(CommandHandler("ping", basic.ping))
    app.add_handler(
        CallbackQueryHandler(onboarding.attest_callback, pattern=f"^{onboarding.ATTEST_CALLBACK}$")
    )

    if app.job_queue is not None:
        app.job_queue.run_repeating(deposits.scan_job, interval=DEPOSIT_SCAN_INTERVAL_S, first=10)
    return app
