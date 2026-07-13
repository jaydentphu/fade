"""Pluggable error reporting (Stage 12 ships the concrete sinks).

The global error handler calls ErrorReporter.report on every unhandled
exception. `build_reporter` wires the real sinks from configuration:
SENTRY_DSN -> Sentry, FADE_ADMIN_USER_ID -> throttled admin DMs, both, or
the no-op default when neither is set (so dev needs zero new env vars).

Contract: report receives the exception and the affected user id only —
never Update contents, never key material.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# One admin DM per error signature per hour: a crash-loop produces one alert,
# not a flooded Telegram chat. New signatures always alert immediately.
ADMIN_DM_THROTTLE_S = 3600.0

ADMIN_DM_TEXT = "⚠️ Unhandled error (user {user_id}): {signature}"


@runtime_checkable
class ErrorReporter(Protocol):
    def report(self, error: BaseException, *, user_id: int | None) -> object:
        """Record an unhandled exception. May be sync or async."""
        ...


class NullErrorReporter:
    """Default: does nothing. Keeps the handler path uniform in dev/tests."""

    def report(self, error: BaseException, *, user_id: int | None) -> None:
        return None


def _signature(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:200]


class AdminDmReporter:
    """DMs the admin about unhandled errors, throttled per error signature.

    Receives only the exception and user id (the ErrorReporter contract), so
    the DM can never contain Update contents or key material. `send` is the
    bot's async send_message; `now` is injectable for tests.
    """

    def __init__(
        self,
        send: Callable[..., Any],
        admin_user_id: int,
        now: Callable[[], float] = time.monotonic,
        throttle_s: float = ADMIN_DM_THROTTLE_S,
    ) -> None:
        self._send = send
        self._admin = admin_user_id
        self._now = now
        self._throttle_s = throttle_s
        self._last_sent: dict[str, float] = {}

    async def report(self, error: BaseException, *, user_id: int | None) -> None:
        signature = _signature(error)
        now = self._now()
        last = self._last_sent.get(signature)
        if last is not None and now - last < self._throttle_s:
            return
        self._last_sent[signature] = now
        try:
            await self._send(
                chat_id=self._admin,
                text=ADMIN_DM_TEXT.format(user_id=user_id, signature=signature),
            )
        except Exception:
            # An alerting failure must never cascade out of the error handler.
            logger.exception("admin error DM failed")


class SentryReporter:
    """Thin Sentry sink. The sdk is an optional dependency (`sentry` extra);
    `sdk` is injectable so tests never import or touch a transport."""

    def __init__(self, dsn: str, sdk: Any | None = None) -> None:
        if sdk is None:  # pragma: no cover - exercised only with the extra installed
            import sentry_sdk as sdk

            sdk.init(dsn=dsn)
        self._sdk = sdk

    def report(self, error: BaseException, *, user_id: int | None) -> None:
        self._sdk.set_user({"id": user_id} if user_id is not None else None)
        self._sdk.capture_exception(error)


class CompositeReporter:
    """Fans out to several sinks; one sink failing never silences the others."""

    def __init__(self, *reporters: ErrorReporter) -> None:
        self._reporters = reporters

    async def report(self, error: BaseException, *, user_id: int | None) -> None:
        for reporter in self._reporters:
            await report_safely(reporter, error, user_id=user_id)


def build_reporter(settings, send_message: Callable[..., Any]) -> ErrorReporter:
    """Assemble the configured sinks. Nothing configured = the no-op default,
    so dev/polling mode works with zero new required env vars."""
    reporters: list[ErrorReporter] = []
    if settings.sentry_dsn:
        reporters.append(SentryReporter(settings.sentry_dsn))
    if settings.admin_user_id:
        reporters.append(AdminDmReporter(send_message, settings.admin_user_id))
    if not reporters:
        return NullErrorReporter()
    if len(reporters) == 1:
        return reporters[0]
    return CompositeReporter(*reporters)


async def report_safely(
    reporter: ErrorReporter | None, error: BaseException, *, user_id: int | None = None
) -> None:
    """Report an error without ever raising: background loops call this after
    logging so a broken reporter can't take down the loop it's watching."""
    if reporter is None:
        return
    try:
        maybe = reporter.report(error, user_id=user_id)
        if inspect.isawaitable(maybe):
            await maybe
    except Exception:
        logger.exception("error reporter itself failed")
