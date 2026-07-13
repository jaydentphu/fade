"""Trade broadcasts to clans (PRD F-17, F-18) with per-chat rate limiting.

Telegram allows ~20 messages/minute per group; the queue enforces that per
chat so a burst of trades never gets the bot muted (PRD §6 Performance).

Group-rendered text NEVER includes balances or wallet addresses (PRD F-7).
"""

from __future__ import annotations

import inspect
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from telegram.error import Forbidden

from fade.bot.handlers.common import sanitize
from fade.db.models import ClanMember, User
from fade.errors import ErrorReporter, report_safely

logger = logging.getLogger(__name__)

PER_CHAT_PER_MINUTE = 20
WINDOW_S = 60.0
# Telegram's global bot limit is ~30 messages/second across ALL chats (PRD §6).
GLOBAL_PER_SECOND = 30
GLOBAL_WINDOW_S = 1.0
# Bounded retry (Stage 11): a message that fails to send is re-queued at the
# back of its chat's queue and dropped for good after this many attempts, so a
# poisoned message can never block a chat forever.
MAX_SEND_ATTEMPTS = 3

TRADE_CARD_TEXT = "{who} {action} {size}on:\n{question}\nEntry: {price}"
SELL_CARD_TEXT = "{who} sold {size}of:\n{question}\nExit: {price}"


@dataclass
class PendingMessage:
    chat_id: int
    text: str
    reply_markup: Any = None
    reply_to_message_id: int | None = None
    on_sent: Callable[[Any], None] | None = None  # called with the sent Message
    attempts: int = 0  # failed send attempts so far (see MAX_SEND_ATTEMPTS)


@dataclass
class BroadcastQueue:
    """Per-chat FIFO queues drained at most PER_CHAT_PER_MINUTE per chat.

    `send` and `now` are injectable for tests: no live network, fake clock.
    """

    send: Callable[..., Any]  # async callable(chat_id=, text=, ...)
    now: Callable[[], float] = time.monotonic
    # Called with the chat_id when a send fails with Forbidden (bot kicked):
    # app wiring points this at clan deactivation. The chat's queue is dropped.
    on_forbidden: Callable[[int], Awaitable[None]] | None = None
    reporter: ErrorReporter | None = None
    _queues: dict[int, deque[PendingMessage]] = field(default_factory=dict)
    _sent_at: dict[int, deque[float]] = field(default_factory=dict)
    _sent_global: deque[float] = field(default_factory=deque)

    def enqueue(
        self, message: PendingMessage, on_sent: Callable[[Any], None] | None = None
    ) -> None:
        if on_sent is not None:
            message.on_sent = on_sent
        self._queues.setdefault(message.chat_id, deque()).append(message)

    def pending_count(self, chat_id: int) -> int:
        return len(self._queues.get(chat_id, ()))

    def _budget(self, chat_id: int) -> int:
        cutoff = self.now() - WINDOW_S
        sent = self._sent_at.setdefault(chat_id, deque())
        while sent and sent[0] <= cutoff:
            sent.popleft()
        return PER_CHAT_PER_MINUTE - len(sent)

    def _global_budget(self) -> int:
        cutoff = self.now() - GLOBAL_WINDOW_S
        while self._sent_global and self._sent_global[0] <= cutoff:
            self._sent_global.popleft()
        return GLOBAL_PER_SECOND - len(self._sent_global)

    async def drain_once(self) -> int:
        """Send what the per-chat AND global rate budgets allow. Returns sent count.

        Failure isolation (Stage 11): a raising send is logged + reported and
        the drain continues; the message is re-queued at the back of its chat's
        queue and dropped after MAX_SEND_ATTEMPTS. Forbidden (bot kicked) drops
        the whole chat's queue and triggers on_forbidden cleanup. Attempts count
        against the rate budgets — a failed API call is still an API call.
        """
        delivered = 0
        for chat_id, queue in list(self._queues.items()):
            budget = self._budget(chat_id)
            while queue and budget > 0 and self._global_budget() > 0:
                message = queue.popleft()
                stamp = self.now()
                self._sent_at[chat_id].append(stamp)
                self._sent_global.append(stamp)
                budget -= 1
                try:
                    sent = await self.send(
                        chat_id=message.chat_id,
                        text=message.text,
                        reply_markup=message.reply_markup,
                        reply_to_message_id=message.reply_to_message_id,
                    )
                except Forbidden as error:
                    logger.warning("bot removed from chat %s; dropping its queue", chat_id)
                    await report_safely(self.reporter, error)
                    queue.clear()
                    if self.on_forbidden is not None:
                        try:
                            await self.on_forbidden(chat_id)
                        except Exception as cleanup_error:
                            logger.exception("forbidden-cleanup failed for chat %s", chat_id)
                            await report_safely(self.reporter, cleanup_error)
                    break
                except Exception as error:
                    message.attempts += 1
                    logger.exception(
                        "broadcast send failed for chat %s (attempt %d/%d)",
                        chat_id,
                        message.attempts,
                        MAX_SEND_ATTEMPTS,
                    )
                    await report_safely(self.reporter, error)
                    if message.attempts < MAX_SEND_ATTEMPTS:
                        queue.append(message)  # retry behind the rest of the chat
                    else:
                        logger.error(
                            "dropping broadcast to chat %s after %d failed attempts",
                            chat_id,
                            MAX_SEND_ATTEMPTS,
                        )
                    continue
                if message.on_sent is not None:
                    result = message.on_sent(sent)
                    if inspect.isawaitable(result):
                        await result
                delivered += 1
            if not queue:
                self._queues.pop(chat_id, None)
        return delivered


def render_trade_card(
    *,
    who: str,
    question: str,
    side_label: str,
    amount_usdc: Decimal | None,
    price: Decimal | None,
    hide_amounts: bool,
    is_sell: bool = False,
) -> str:
    """Group-safe trade card: side + market always shown; amount only if allowed.

    No balances. No wallet addresses. Ever.
    """
    size = "" if hide_amounts or amount_usdc is None else f"${amount_usdc} "
    price_text = f"{price}" if price is not None else "?"
    template = SELL_CARD_TEXT if is_sell else TRADE_CARD_TEXT
    action = f"sold {side_label}" if is_sell else f"bought {side_label}"
    if is_sell:
        return template.format(
            who=sanitize(who, limit=40),
            size=size,
            question=sanitize(question),
            price=price_text,
        )
    return template.format(
        who=sanitize(who, limit=40),
        action=action,
        size=size,
        question=sanitize(question),
        price=price_text,
    )


async def clans_of_user(session_factory, telegram_user_id: int) -> list[int]:
    async with session_factory() as session:
        rows = await session.scalars(
            select(ClanMember.chat_id).where(ClanMember.telegram_user_id == telegram_user_id)
        )
        return list(rows)


async def hide_amounts_for(session_factory, telegram_user_id: int) -> bool:
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    return bool(user and user.hide_amounts)
