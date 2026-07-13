"""Pluggable error reporting.

The global error handler calls ErrorReporter.report on every unhandled
exception. The default is a no-op; a production build can set
app.bot_data["error_reporter"] to a Sentry-backed (or any) implementation
without touching handler code.

Contract: report receives the exception and the affected user id only —
never Update contents, never key material.
"""

from __future__ import annotations

import inspect
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ErrorReporter(Protocol):
    def report(self, error: BaseException, *, user_id: int | None) -> object:
        """Record an unhandled exception. May be sync or async."""
        ...


class NullErrorReporter:
    """Default: does nothing. Keeps the handler path uniform in dev/tests."""

    def report(self, error: BaseException, *, user_id: int | None) -> None:
        return None


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
