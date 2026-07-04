"""Deposit scanner: detection, confirmation DM, and no double-crediting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select

from fade.bot.handlers.deposits import TRANSFER_TOPIC, DepositScanner, _pad_address
from fade.db.models import Deposit, User, Wallet

WALLET_ADDR = "0xFadE0000000000000000000000000000000000aa"
SENDER_ADDR = "0x1111111111111111111111111111111111111111"
PUSD = "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"
TX_HASH = "0x" + "ab" * 32


def make_log(amount_base_units: int, block: int) -> dict:
    return {
        "transactionHash": TX_HASH,
        "address": PUSD,
        "topics": [TRANSFER_TOPIC, _pad_address(SENDER_ADDR), _pad_address(WALLET_ADDR)],
        "data": hex(amount_base_units),
        "blockNumber": hex(block),
    }


class FakeRpc:
    def __init__(self, heads: list[int], logs_per_scan: list[list[dict]]) -> None:
        self._heads = iter(heads)
        self._logs = iter(logs_per_scan)
        self.log_queries: list[tuple[int, int]] = []

    async def block_number(self) -> int:
        return next(self._heads)

    async def transfers_to(self, padded, from_block: int, to_block: int) -> list[dict]:
        self.log_queries.append((from_block, to_block))
        return next(self._logs, [])


async def seed_user_wallet(session_factory) -> None:
    async with session_factory() as session:
        session.add(User(telegram_user_id=501))
        session.add(Wallet(telegram_user_id=501, deposit_wallet_address=WALLET_ADDR))
        await session.commit()


async def test_deposit_detected_confirmed_and_never_double_credited(session_factory):
    await seed_user_wallet(session_factory)
    # Scan 1 initializes the watermark (head 100). Scan 2 (head 150) finds a
    # transfer at block 120 — already 31 confirmations deep, so it confirms and
    # DMs. Scan 3 (head 160) returns the SAME log again (overlap/retry): must
    # be ignored, no second DM.
    rpc = FakeRpc(
        heads=[100, 150, 160],
        logs_per_scan=[[make_log(2_500_000, 120)], [make_log(2_500_000, 120)]],
    )
    scanner = DepositScanner(rpc, session_factory, confirmations=12)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await scanner.scan_once(bot)  # init watermark
    bot.send_message.assert_not_awaited()

    await scanner.scan_once(bot)  # detect + confirm
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 501
    assert "2.5" in kwargs["text"]
    assert "pUSD" in kwargs["text"]

    await scanner.scan_once(bot)  # duplicate log must not re-credit
    bot.send_message.assert_awaited_once()

    async with session_factory() as session:
        deposits = (await session.scalars(select(Deposit))).all()
    assert len(deposits) == 1
    assert deposits[0].status == "confirmed"


async def test_pending_deposit_not_confirmed_before_threshold(session_factory):
    await seed_user_wallet(session_factory)
    # Head 150, deposit at block 145: only 6 confirmations — stays pending.
    rpc = FakeRpc(heads=[100, 150], logs_per_scan=[[make_log(1_000_000, 145)]])
    scanner = DepositScanner(rpc, session_factory, confirmations=12)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await scanner.scan_once(bot)
    await scanner.scan_once(bot)

    bot.send_message.assert_not_awaited()
    async with session_factory() as session:
        deposit = await session.scalar(select(Deposit))
    assert deposit is not None and deposit.status == "pending"


async def test_scan_advances_watermark_without_wallets(session_factory):
    rpc = FakeRpc(heads=[100, 150], logs_per_scan=[])
    scanner = DepositScanner(rpc, session_factory, confirmations=12)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    await scanner.scan_once(bot)
    await scanner.scan_once(bot)

    assert rpc.log_queries == []  # no wallets -> no log queries
