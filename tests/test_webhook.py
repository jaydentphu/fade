"""Webhook receiver: secret verification and forwarding (no live network)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fade.webhook import handle_webhook_request

SECRET = "s3cr3t-webhook-token"
# Minimal valid update: update_id alone parses; message parsing needs more
# fields than this test cares about, and forwarding is what's under test.
UPDATE_BODY = {"update_id": 1}


def make_application() -> MagicMock:
    app = MagicMock()
    app.bot = MagicMock()
    app.process_update = AsyncMock()
    return app


async def test_missing_secret_is_rejected_without_touching_application():
    app = make_application()
    response = await handle_webhook_request(app, SECRET, None, UPDATE_BODY)
    assert response.status_code == 401
    app.process_update.assert_not_awaited()


async def test_wrong_secret_is_rejected_without_touching_application():
    app = make_application()
    response = await handle_webhook_request(app, SECRET, "wrong-token", UPDATE_BODY)
    assert response.status_code == 401
    app.process_update.assert_not_awaited()


async def test_blank_configured_secret_rejects_everything():
    # A misconfigured (empty) prod secret must fail closed, not open.
    app = make_application()
    response = await handle_webhook_request(app, "", "", UPDATE_BODY)
    assert response.status_code == 401
    app.process_update.assert_not_awaited()


async def test_valid_secret_forwards_into_process_update():
    app = make_application()
    response = await handle_webhook_request(app, SECRET, SECRET, UPDATE_BODY)
    assert response.status_code == 200
    app.process_update.assert_awaited_once()


# -- Stage 11: malformed bodies are 400, never 500, never processed --------------


async def test_invalid_json_body_is_rejected_400():
    from fade.webhook import handle_webhook_raw

    app = make_application()
    response = await handle_webhook_raw(app, SECRET, SECRET, b"{this is not json")
    assert response.status_code == 400
    app.process_update.assert_not_awaited()


async def test_implausible_update_body_is_rejected_400():
    app = make_application()
    for body in ({"foo": "bar"}, {"update_id": "not-an-int"}, ["update_id", 1], "hi"):
        response = await handle_webhook_request(app, SECRET, SECRET, body)
        assert response.status_code == 400, body
    app.process_update.assert_not_awaited()


async def test_secret_is_checked_before_json_parsing():
    from fade.webhook import handle_webhook_raw

    app = make_application()
    response = await handle_webhook_raw(app, SECRET, "wrong", b"{broken")
    assert response.status_code == 401  # auth rejection wins; body never parsed
    app.process_update.assert_not_awaited()
