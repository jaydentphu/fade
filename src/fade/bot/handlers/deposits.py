"""Background deposit scanner (PRD F-3, D-8).

Polls Polygon for pUSD/USDC transfers into any registered deposit wallet,
records them, and DMs a plain confirmation after 12 confirmations. Restart-safe:
the last scanned block is persisted, and tx hashes are unique in the deposits
table so nothing is credited twice.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram.ext import ContextTypes

from fade.db.models import ChainScanState, Deposit, Wallet
from fade.errors import ErrorReporter, report_safely

logger = logging.getLogger(__name__)

TOKENS = {
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e",
    "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
    "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb": "pUSD",
}
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOKEN_DECIMALS = Decimal(10) ** 6
MAX_LOG_RANGE = 50  # keyless public RPCs cap eth_getLogs ranges hard

DEPOSIT_CONFIRMED_TEXT = "Deposit confirmed: ${amount} ({token}). Balance updates shortly."


def _pad_address(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


class Rpc:
    def __init__(self, url: str) -> None:
        self._url = url
        self._id = 0

    async def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self._url,
                json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
            )
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise RuntimeError(f"RPC error from {method}: {payload['error']}")
        return payload["result"]

    async def block_number(self) -> int:
        return int(await self.call("eth_blockNumber", []), 16)

    async def transfers_to(
        self, padded_recipients: list[str], from_block: int, to_block: int
    ) -> list[dict[str, Any]]:
        logs: list[dict[str, Any]] = []
        start = from_block
        while start <= to_block:
            end = min(start + MAX_LOG_RANGE - 1, to_block)
            logs.extend(
                await self.call(
                    "eth_getLogs",
                    [
                        {
                            "fromBlock": hex(start),
                            "toBlock": hex(end),
                            "address": list(TOKENS),
                            "topics": [TRANSFER_TOPIC, None, padded_recipients],
                        }
                    ],
                )
            )
            start = end + 1
        return logs


class DepositScanner:
    def __init__(
        self,
        rpc: Rpc,
        session_factory: async_sessionmaker[AsyncSession],
        confirmations: int = 12,
        reporter: ErrorReporter | None = None,
    ) -> None:
        self._rpc = rpc
        self._sessions = session_factory
        self._confirmations = confirmations
        self._reporter = reporter

    async def scan_once(self, bot: Any) -> None:
        head = await self._rpc.block_number()
        async with self._sessions() as session:
            state = await session.get(ChainScanState, 1)
            if state is None:
                # First run: start at the head, no backfill.
                session.add(ChainScanState(id=1, last_scanned_block=head))
                await session.commit()
                return
            from_block = state.last_scanned_block + 1

            wallets = (await session.scalars(select(Wallet))).all()
            address_to_user = {
                w.deposit_wallet_address.lower(): w.telegram_user_id for w in wallets
            }

            if address_to_user and head >= from_block:
                await self._record_new_transfers(session, address_to_user, from_block, head)
            state.last_scanned_block = head
            await session.commit()

            await self._confirm_pending(session, bot, head)
            await session.commit()

    async def _record_new_transfers(
        self,
        session: AsyncSession,
        address_to_user: dict[str, int],
        from_block: int,
        to_block: int,
    ) -> None:
        padded = [_pad_address(addr) for addr in address_to_user]
        logs = await self._rpc.transfers_to(padded, from_block, to_block)
        for log in logs:
            tx_hash = log["transactionHash"]
            seen = await session.scalar(select(Deposit).where(Deposit.tx_hash == tx_hash))
            if seen is not None:
                continue
            recipient = "0x" + log["topics"][2][-40:]
            user_id = address_to_user.get(recipient.lower())
            if user_id is None:
                continue
            session.add(
                Deposit(
                    telegram_user_id=user_id,
                    tx_hash=tx_hash,
                    token=TOKENS.get(log["address"].lower(), log["address"]),
                    amount=Decimal(int(log["data"], 16)) / TOKEN_DECIMALS,
                    block_number=int(log["blockNumber"], 16),
                    status="pending",
                )
            )

    async def _confirm_pending(self, session: AsyncSession, bot: Any, head: int) -> None:
        pending = (await session.scalars(select(Deposit).where(Deposit.status == "pending"))).all()
        for deposit in pending:
            if head - deposit.block_number + 1 < self._confirmations:
                continue
            # Send BEFORE marking: a failed DM (user blocked the bot, Telegram
            # hiccup) leaves the deposit pending so the next scan retries it,
            # and one user's failure never blocks the rest of the loop.
            try:
                await bot.send_message(
                    chat_id=deposit.telegram_user_id,
                    text=DEPOSIT_CONFIRMED_TEXT.format(amount=deposit.amount, token=deposit.token),
                )
            except Exception as error:
                logger.exception(
                    "deposit confirm DM failed for user %s (tx %s); will retry next scan",
                    deposit.telegram_user_id,
                    deposit.tx_hash,
                )
                await report_safely(self._reporter, error, user_id=deposit.telegram_user_id)
                continue
            deposit.status = "confirmed"
            deposit.confirmed_at = datetime.now(UTC)


async def scan_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    scanner: DepositScanner = context.application.bot_data["deposit_scanner"]
    await scanner.scan_once(context.bot)
