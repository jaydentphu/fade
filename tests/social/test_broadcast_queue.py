"""BroadcastQueue: per-chat rate limiting with fake clock and fake sender."""

from __future__ import annotations

from fade.social.broadcast import PER_CHAT_PER_MINUTE, BroadcastQueue, PendingMessage

CHAT_A = -100
CHAT_B = -200


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_queue():
    sent: list[dict] = []

    async def send(**kwargs):
        sent.append(kwargs)
        return type("Msg", (), {"message_id": len(sent)})()

    clock = FakeClock()
    return BroadcastQueue(send=send, now=clock), sent, clock


async def test_queue_caps_at_twenty_messages_per_minute_per_group():
    """PRD §6: no more than 20 messages/minute per group leave the queue."""
    queue, sent, clock = make_queue()
    for i in range(30):
        queue.enqueue(PendingMessage(chat_id=CHAT_A, text=f"msg {i}"))

    await queue.drain_once()
    assert len(sent) == PER_CHAT_PER_MINUTE  # 20 out, 10 held back

    clock.advance(30)
    await queue.drain_once()
    assert len(sent) == PER_CHAT_PER_MINUTE  # still inside the window: nothing more

    clock.advance(31)  # window expires
    await queue.drain_once()
    assert len(sent) == 30  # the remaining 10 flow out


async def test_rate_limits_are_independent_per_chat():
    queue, sent, _ = make_queue()
    for i in range(25):
        queue.enqueue(PendingMessage(chat_id=CHAT_A, text=f"a{i}"))
        queue.enqueue(PendingMessage(chat_id=CHAT_B, text=f"b{i}"))

    await queue.drain_once()

    a_sent = [m for m in sent if m["chat_id"] == CHAT_A]
    b_sent = [m for m in sent if m["chat_id"] == CHAT_B]
    assert len(a_sent) == PER_CHAT_PER_MINUTE
    assert len(b_sent) == PER_CHAT_PER_MINUTE


async def test_messages_stay_in_order():
    queue, sent, _ = make_queue()
    for i in range(5):
        queue.enqueue(PendingMessage(chat_id=CHAT_A, text=f"msg {i}"))

    await queue.drain_once()

    assert [m["text"] for m in sent] == [f"msg {i}" for i in range(5)]


async def test_on_sent_callback_receives_sent_message():
    queue, _, _ = make_queue()
    seen = []
    queue.enqueue(PendingMessage(chat_id=CHAT_A, text="card"), on_sent=seen.append)

    await queue.drain_once()

    assert len(seen) == 1
    assert seen[0].message_id == 1
