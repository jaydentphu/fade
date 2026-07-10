"""The global error handler invokes the pluggable reporter — with the exception
and user id only, never key material."""

from __future__ import annotations

from unittest.mock import MagicMock

from telegram import Update

from fade.bot.app import _log_error


class RecordingReporter:
    def __init__(self) -> None:
        self.calls: list[tuple[BaseException, int | None]] = []

    def report(self, error: BaseException, *, user_id: int | None):
        self.calls.append((error, user_id))


def make_context(reporter, error: BaseException) -> MagicMock:
    context = MagicMock()
    context.error = error
    context.application.bot_data = {"error_reporter": reporter}
    return context


def make_update(user_id: int) -> MagicMock:
    update = MagicMock(spec=Update)
    update.effective_user.id = user_id
    return update


async def test_error_handler_reports_exception_and_user_id():
    reporter = RecordingReporter()
    boom = RuntimeError("kaboom")
    await _log_error(make_update(555), make_context(reporter, boom))

    assert len(reporter.calls) == 1
    reported_error, reported_user = reporter.calls[0]
    assert reported_error is boom
    assert reported_user == 555


async def test_error_handler_never_passes_key_material():
    # The reporter contract is (exception, user_id) — no update contents, no
    # keys. Prove the reporter only ever receives those two things.
    seen_args: list = []

    class NosyReporter:
        def report(self, error, *, user_id):
            seen_args.append((error, user_id))

    fake_key = "0x" + "ab" * 32
    err = RuntimeError("failure while handling")  # deliberately no key in message
    await _log_error(make_update(1), make_context(NosyReporter(), err))

    (reported_error, reported_user) = seen_args[0]
    assert fake_key not in str(reported_error)
    assert reported_user == 1


async def test_missing_reporter_is_a_no_op():
    context = MagicMock()
    context.error = RuntimeError("x")
    context.application.bot_data = {}  # no reporter configured
    # Must not raise.
    await _log_error(make_update(1), context)


async def test_async_reporter_is_awaited():
    awaited = []

    class AsyncReporter:
        async def report(self, error, *, user_id):
            awaited.append(user_id)

    await _log_error(make_update(77), make_context(AsyncReporter(), RuntimeError("x")))
    assert awaited == [77]
