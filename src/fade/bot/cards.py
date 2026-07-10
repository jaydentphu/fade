"""Durable store for in-flight interactive cards (market, position, fade).

Memory is the fast path; the database is the restart-survival path. A process
restart loses the in-memory dict but not the DB rows, so a card posted before
a redeploy still works after it — up to a TTL, past which the user gets the
normal "card expired" message instead of a crash.

Cards contain market/position/broadcast context only. No key material.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fade.db.models import UiCard

CARD_TTL = timedelta(hours=24)


def _encode(obj: Any) -> str:
    def default(value: Any):
        if isinstance(value, Decimal):
            return {"__decimal__": str(value)}
        raise TypeError(f"unserializable card field: {type(value)}")

    return json.dumps(asdict(obj), default=default)


def _decode_fields(data: dict) -> dict:
    return {
        k: Decimal(v["__decimal__"]) if isinstance(v, dict) and "__decimal__" in v else v
        for k, v in data.items()
    }


def _revive(kind: str, payload: str) -> Any:
    # Imports are local to avoid a cycle: handlers import this module.
    data = _decode_fields(json.loads(payload))
    if kind == "market":
        from fade.trading.markets import MarketCard

        return MarketCard(**data)
    if kind == "position":
        from fade.trading.service import PositionView

        return PositionView(**data)
    if kind == "fade":
        from fade.bot.handlers.clan import FadeCard

        return FadeCard(**data)
    raise ValueError(f"unknown card kind: {kind}")


class CardStore:
    """L1 in-memory + optional L2 database card storage with a TTL.

    `session_factory=None` gives a memory-only store (used by most unit tests);
    production wires a real factory so cards survive restarts.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        ttl: timedelta = CARD_TTL,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._ttl = ttl
        self._now = now or (lambda: datetime.now(UTC))
        self._memory: dict[str, tuple[str, Any, datetime]] = {}  # key -> (kind, obj, born)

    async def put(self, kind: str, obj: Any) -> str:
        key = secrets.token_hex(4)
        self._memory[key] = (kind, obj, self._now())
        if self._sessions is not None:
            async with self._sessions() as session:
                session.add(
                    UiCard(
                        key=key,
                        kind=kind,
                        payload=_encode(obj),
                        created_at=self._now().replace(tzinfo=None),
                    )
                )
                await session.commit()
        return key

    async def get(self, kind: str, key: str) -> Any | None:
        cutoff = self._now() - self._ttl
        hit = self._memory.get(key)
        if hit is not None:
            hit_kind, obj, born = hit
            if hit_kind != kind or born < cutoff:
                return None
            return obj
        if self._sessions is None:
            return None
        async with self._sessions() as session:
            row = await session.get(UiCard, key)
        if row is None or row.kind != kind:
            return None
        born = (
            row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at
        )
        if born < cutoff:
            return None
        obj = _revive(kind, row.payload)
        self._memory[key] = (kind, obj, born)
        return obj

    async def update(self, kind: str, key: str, obj: Any) -> None:
        """Persist a mutated card (e.g. a fade card learning its message_id)."""
        hit = self._memory.get(key)
        born = hit[2] if hit is not None else self._now()
        self._memory[key] = (kind, obj, born)
        if self._sessions is None:
            return
        async with self._sessions() as session:
            row = await session.get(UiCard, key)
            if row is not None:
                row.payload = _encode(obj)
                await session.commit()


def get_store(context) -> CardStore:
    """The application's card store; creates a memory-only one if unwired."""
    store = context.application.bot_data.get("cards")
    if store is None:
        store = CardStore()
        context.application.bot_data["cards"] = store
    return store
