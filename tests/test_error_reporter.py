"""The global error handler invokes the pluggable reporter — with the exception
and user id only, never key material. Stage 12 adds the concrete sinks:
throttled admin DMs, an injectable Sentry wrapper, and config-driven assembly."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.app import _log_error
from fade.errors import (
    AdminDmReporter,
    CompositeReporter,
    NullErrorReporter,
    SentryReporter,
    build_reporter,
)


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


# -- Stage 12: concrete sinks -----------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def test_unhandled_exception_reaches_admin_dm_with_no_update_contents():
    """End to end: _log_error -> AdminDmReporter -> one DM carrying only the
    exception signature and user id — never anything from the Update."""
    send = AsyncMock()
    reporter = AdminDmReporter(send, admin_user_id=1, now=FakeClock())
    update = make_update(555)
    update.message = MagicMock()
    update.message.text = "SECRET-DM-CONTENTS 0x" + "ab" * 32

    await _log_error(update, make_context(reporter, RuntimeError("kaboom")))

    send.assert_awaited_once()
    text = send.call_args.kwargs["text"]
    assert "RuntimeError" in text and "kaboom" in text
    assert "555" in text  # the affected user id
    assert "SECRET-DM-CONTENTS" not in text  # no Update contents
    assert "0x" not in text  # no key-shaped material


async def test_admin_dm_throttles_identical_errors_inside_window():
    send = AsyncMock()
    clock = FakeClock()
    reporter = AdminDmReporter(send, admin_user_id=1, now=clock, throttle_s=3600)

    await reporter.report(RuntimeError("same failure"), user_id=1)
    clock.t += 60
    await reporter.report(RuntimeError("same failure"), user_id=2)  # inside window

    send.assert_awaited_once()  # two identical errors -> one DM

    clock.t += 3600  # window expires
    await reporter.report(RuntimeError("same failure"), user_id=3)
    assert send.await_count == 2  # alerts again after the window


async def test_admin_dm_does_not_throttle_different_errors():
    send = AsyncMock()
    reporter = AdminDmReporter(send, admin_user_id=1, now=FakeClock())

    await reporter.report(RuntimeError("failure A"), user_id=1)
    await reporter.report(ValueError("failure B"), user_id=1)

    assert send.await_count == 2  # different signatures both alert


async def test_admin_dm_send_failure_never_raises():
    send = AsyncMock(side_effect=RuntimeError("telegram down"))
    reporter = AdminDmReporter(send, admin_user_id=1, now=FakeClock())
    await reporter.report(RuntimeError("x"), user_id=1)  # must not raise


async def test_sentry_reporter_captures_exception_and_user(settings):
    """Injectable sdk: no import of sentry-sdk, no transport, no network."""
    sdk = MagicMock()
    reporter = SentryReporter("https://fake@dsn/1", sdk=sdk)
    boom = RuntimeError("boom")

    reporter.report(boom, user_id=42)

    sdk.set_user.assert_called_once_with({"id": 42})
    sdk.capture_exception.assert_called_once_with(boom)


async def test_build_reporter_selects_sinks_from_configuration(settings, monkeypatch):
    import fade.errors as errors_module

    send = AsyncMock()
    # Nothing configured -> the no-op default (dev needs zero new env vars).
    bare = replace(settings, admin_user_id=0, sentry_dsn="")
    assert isinstance(build_reporter(bare, send), NullErrorReporter)

    # Admin only -> throttled DMs.
    admin_only = replace(settings, admin_user_id=7, sentry_dsn="")
    assert isinstance(build_reporter(admin_only, send), AdminDmReporter)

    # Sentry only / both — with the SDK wrapper stubbed so no import happens.
    class StubSentry:
        def __init__(self, dsn, sdk=None):
            self.dsn = dsn

        def report(self, error, *, user_id):
            pass

    monkeypatch.setattr(errors_module, "SentryReporter", StubSentry)
    sentry_only = replace(settings, admin_user_id=0, sentry_dsn="https://fake@dsn/1")
    assert isinstance(errors_module.build_reporter(sentry_only, send), StubSentry)

    both = replace(settings, admin_user_id=7, sentry_dsn="https://fake@dsn/1")
    assert isinstance(errors_module.build_reporter(both, send), CompositeReporter)


async def test_composite_reporter_fans_out_and_isolates_failures():
    calls = []

    class Broken:
        def report(self, error, *, user_id):
            raise RuntimeError("this sink is down")

    class Working:
        def report(self, error, *, user_id):
            calls.append(user_id)

    composite = CompositeReporter(Broken(), Working())
    await composite.report(RuntimeError("x"), user_id=9)

    assert calls == [9]  # one broken sink never silences the other
