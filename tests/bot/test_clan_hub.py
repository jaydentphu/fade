"""/clan, /clans, invites and member management through the real handlers,
with mocked Telegram Update/Context objects (D-15). No network."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram import Update

from fade.bot.handlers import clan as group_clan
from fade.bot.handlers import clan_hub, onboarding, router
from fade.db.models import GroupChat
from fade.social import clans
from fade.social.broadcast import BroadcastQueue

OWNER, ALICE, BOB, CAROL = 501, 502, 503, 504
HOME = -100900


def make_context(session_factory, *, args=None):
    context = MagicMock()
    context.bot.username = "fadetest_bot"
    context.bot.send_message = AsyncMock()
    context.user_data = {}
    context.args = args or []
    context.application.bot_data = {
        "sessions": session_factory,
        "broadcasts": BroadcastQueue(send=AsyncMock()),
    }
    return context


def make_message_update(user_id, *, chat_type="private", text="", chat_id=None):
    update = MagicMock(spec=Update)
    update.effective_user.id = user_id
    update.effective_user.username = f"u{user_id}"
    update.effective_user.first_name = "Tester"
    update.effective_chat.type = chat_type
    update.effective_chat.id = chat_id if chat_id is not None else user_id
    update.effective_chat.title = "Shark Chat"
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    update.message.reply_to_message = None
    update.callback_query = None
    return update


def make_callback(user_id, data, *, chat_id=None):
    update = MagicMock(spec=Update)
    update.effective_user.id = user_id
    update.effective_user.username = f"u{user_id}"
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message.photo = None  # a text message: edit_card edits its text
    query.message.chat.id = chat_id if chat_id is not None else user_id
    query.message.reply_text = AsyncMock()
    query.from_user.username = f"u{user_id}"
    update.callback_query = query
    return update, query


def last_text(mock) -> str:
    call = mock.call_args
    return call.args[0] if call.args else call.kwargs.get("text", "")


def buttons(mock) -> list[tuple[str, str]]:
    markup = mock.call_args.kwargs["reply_markup"]
    return [(b.text, b.callback_data or b.url) for row in markup.inline_keyboard for b in row]


async def tap(context, user_id, data, **kwargs):
    update, query = make_callback(user_id, data, **kwargs)
    if data.startswith("cl:"):
        await clan_hub.clan_callback(update, context)
    elif data.startswith("cb:"):
        await clan_hub.board_callback(update, context)
    else:
        await clan_hub.home_callback(update, context)
    return query


async def seed(session_factory, onboard, *, home=None):
    for uid in (OWNER, ALICE, BOB, CAROL):
        await onboard(uid)
    clan = await clans.create(session_factory, OWNER, "Shark Tank", "🦈")
    if home is not None:
        async with session_factory() as session:
            session.add(GroupChat(chat_id=home, title="Shark Chat", created_by=OWNER))
            await session.commit()
        await clans.link_home_chat(session_factory, OWNER, home)
    return clan


# -- create ---------------------------------------------------------------------------


async def test_clanless_user_gets_the_create_button(session_factory, onboard):
    await onboard(ALICE)
    update = make_message_update(ALICE)
    await clan_hub.clan_command(update, make_context(session_factory))
    assert last_text(update.message.reply_text) == clan_hub.NO_CLAN_TEXT
    assert ("⚔️ Create a clan", "cl:create") in buttons(update.message.reply_text)


async def test_clan_requires_an_account(session_factory):
    update = make_message_update(ALICE)
    await clan_hub.clan_command(update, make_context(session_factory))
    assert last_text(update.message.reply_text) == clan_hub.NEED_ACCOUNT_TEXT


async def test_full_create_flow_name_then_icon(session_factory, onboard):
    await onboard(ALICE)
    context = make_context(session_factory)

    query = await tap(context, ALICE, "cl:create")
    assert last_text(query.edit_message_text) == clan_hub.ASK_NAME_TEXT

    # The name arrives as a plain DM, through the real text router.
    reply = make_message_update(ALICE, text="  Shark   Tank ")
    await router.handle_text(reply, context)
    assert last_text(reply.message.reply_text) == "Pick an icon for Shark Tank."
    icons = buttons(reply.message.reply_text)
    assert ("🦈", f"cl:icon:{clans.ICONS.index('🦈')}") in icons

    query = await tap(context, ALICE, f"cl:icon:{clans.ICONS.index('🦈')}")
    text = last_text(query.edit_message_text)
    assert text.startswith("🦈 Shark Tank is born.")
    mine = await clans.current(session_factory, ALICE)
    assert mine[0].name == "Shark Tank" and mine[1].role == clans.OWNER
    assert clan_hub.NEW_NAME_KEY not in context.user_data


async def test_bad_or_taken_names_keep_the_flow_open(session_factory, onboard):
    await seed(session_factory, onboard)
    context = make_context(session_factory)
    await tap(context, ALICE, "cl:create")

    taken = make_message_update(ALICE, text="shark tank")
    await router.handle_text(taken, context)
    assert last_text(taken.message.reply_text) == clans.NAME_TAKEN_ERROR

    bad = make_message_update(ALICE, text="x_x")
    await router.handle_text(bad, context)
    assert last_text(bad.message.reply_text) == clans.NAME_LENGTH_ERROR or "letters" in last_text(
        bad.message.reply_text
    )

    ok = make_message_update(ALICE, text="Wolfpack")
    await router.handle_text(ok, context)
    assert "Pick an icon" in last_text(ok.message.reply_text)


async def test_clan_name_text_is_not_mistaken_for_a_trade_amount(session_factory, onboard):
    """The newest flow owns the reply: starting a clan flow drops a stale
    custom-amount prompt, so 'Wolfpack' is never parsed as dollars."""
    await onboard(ALICE)
    context = make_context(session_factory)
    context.user_data["pending_custom"] = ("cardkey", "y")
    await tap(context, ALICE, "cl:create")
    assert "pending_custom" not in context.user_data


# -- invites and joining ------------------------------------------------------------


async def test_owner_sees_invite_link_members_do_not(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)
    context = make_context(session_factory)

    query = await tap(context, OWNER, "cl:invite")
    assert f"https://t.me/fadetest_bot?start=clan{clan.invite_code}" in last_text(
        query.edit_message_text
    )

    query = await tap(context, ALICE, "cl:invite")
    query.answer.assert_awaited_with(clans.NOT_LEADER_ERROR, show_alert=True)
    query.edit_message_text.assert_not_awaited()


async def test_regenerate_kills_the_old_link(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    context = make_context(session_factory)
    query = await tap(context, OWNER, "cl:regen")
    fresh = (await clans.get_clan(session_factory, clan.id)).invite_code
    assert fresh != clan.invite_code
    assert f"clan{fresh}" in last_text(query.edit_message_text)
    assert await clans.by_invite_code(session_factory, clan.invite_code) is None


async def test_invite_deep_link_for_an_existing_user(session_factory, onboard):
    clan = await seed(session_factory, onboard, home=HOME)
    context = make_context(session_factory, args=[f"clan{clan.invite_code}"])
    context.application.bot_data["wallets"] = SimpleNamespace(
        get_wallet_address=AsyncMock(return_value="0x" + "ab" * 20)
    )
    update = make_message_update(ALICE)
    await onboarding.start(update, context)

    assert last_text(update.message.reply_text).startswith("Join 🦈 Shark Tank?")
    assert ("Join", f"cl:joinok:{clan.invite_code}") in buttons(update.message.reply_text)

    query = await tap(context, ALICE, f"cl:joinok:{clan.invite_code}")
    assert last_text(query.edit_message_text) == "You're in 🦈 Shark Tank."
    assert (await clans.current(session_factory, ALICE))[0].id == clan.id
    queue = context.application.bot_data["broadcasts"]
    assert queue._queues[HOME][-1].text == "⚔️ u502 joined 🦈 Shark Tank."


async def test_invite_link_survives_onboarding(session_factory, onboard):
    """A brand-new user: the code is remembered at /start and offered once
    the wallet exists (resume_pending_invite, called from attestation)."""
    clan = await seed(session_factory, onboard)
    new_user = 777
    context = make_context(session_factory, args=[f"clan{clan.invite_code}"])
    context.application.bot_data["wallets"] = SimpleNamespace(
        get_wallet_address=AsyncMock(return_value=None)
    )
    update = make_message_update(new_user)
    await onboarding.start(update, context)
    assert context.user_data[clan_hub.INVITE_KEY] == clan.invite_code

    await onboard(new_user)
    message = MagicMock()
    message.reply_text = AsyncMock()
    await clan_hub.resume_pending_invite(message, context, new_user)
    assert last_text(message.reply_text).startswith("Join 🦈 Shark Tank?")
    assert clan_hub.INVITE_KEY not in context.user_data


async def test_join_prompt_warns_about_leaving_the_current_clan(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    other = await clans.create(session_factory, CAROL, "Wolfpack", "🐺")
    message = MagicMock()
    message.reply_text = AsyncMock()
    await clans.join(session_factory, ALICE, other.invite_code)
    await clan_hub.offer_join(message, make_context(session_factory), ALICE, clan.invite_code)
    assert "You'll leave 🐺 Wolfpack." in last_text(message.reply_text)


def test_clan_code_parsing():
    assert clan_hub.clan_code_from("clanabc123def0") == "abc123def0"
    assert clan_hub.clan_code_from("CLANABC123") == "abc123"
    assert clan_hub.clan_code_from("clan") is None
    assert clan_hub.clan_code_from("clan../../x") is None
    assert clan_hub.clan_code_from("ref123") is None
    assert clan_hub.clan_code_from(MagicMock()) is None


# -- members, kicks, roles ------------------------------------------------------------


async def test_kick_flow_confirms_then_removes_and_notifies(session_factory, onboard):
    clan = await seed(session_factory, onboard, home=HOME)
    await clans.join(session_factory, ALICE, clan.invite_code)
    context = make_context(session_factory)

    query = await tap(context, OWNER, f"cl:m:{ALICE}")
    actions = buttons(query.edit_message_text)
    assert ("🥾 Kick", f"cl:kick:{ALICE}") in actions
    assert ("⭐ Make officer", f"cl:promote:{ALICE}") in actions

    query = await tap(context, OWNER, f"cl:kick:{ALICE}")
    assert "can't rejoin for 24 hours" in last_text(query.edit_message_text)
    assert await clans.current(session_factory, ALICE) is not None  # not yet

    await tap(context, OWNER, f"cl:kickok:{ALICE}")
    assert await clans.current(session_factory, ALICE) is None
    context.bot.send_message.assert_awaited_with(
        chat_id=ALICE, text=clan_hub.KICKED_DM_TEXT.format(label="🦈 Shark Tank")
    )
    queue = context.application.bot_data["broadcasts"]
    # The onboard fixture stores no username, so the id stands in for it.
    assert queue._queues[HOME][-1].text == "🥾 502 was removed from 🦈 Shark Tank."


async def test_crafted_kick_callback_from_a_member_is_refused(session_factory, onboard):
    """Permissions live in the service: a hand-made callback can't kick."""
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)
    await clans.join(session_factory, BOB, clan.invite_code)
    context = make_context(session_factory)

    query = await tap(context, ALICE, f"cl:kickok:{BOB}")
    query.answer.assert_awaited_with(clans.NOT_LEADER_ERROR, show_alert=True)
    assert await clans.current(session_factory, BOB) is not None


async def test_member_detail_shows_no_actions_to_members(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)
    await clans.join(session_factory, BOB, clan.invite_code)
    query = await tap(make_context(session_factory), ALICE, f"cl:m:{BOB}")
    assert buttons(query.edit_message_text) == [("Back", "cl:members")]


async def test_promote_and_transfer(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)
    context = make_context(session_factory)

    await tap(context, OWNER, f"cl:promote:{ALICE}")
    assert (await clans.current(session_factory, ALICE))[1].role == clans.OFFICER

    await tap(context, OWNER, f"cl:xferok:{ALICE}")
    assert (await clans.current(session_factory, ALICE))[1].role == clans.OWNER
    context.bot.send_message.assert_awaited_with(
        chat_id=ALICE, text="You're now the owner of 🦈 Shark Tank."
    )


async def test_owner_leave_is_blocked_until_transfer(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)
    query = await tap(make_context(session_factory), OWNER, "cl:leave")
    query.answer.assert_awaited_with(clans.OWNER_MUST_TRANSFER_ERROR, show_alert=True)


async def test_disband_notifies_every_member(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)
    context = make_context(session_factory)
    query = await tap(context, OWNER, "cl:disband")
    assert "can't be undone" in last_text(query.edit_message_text)

    await tap(context, OWNER, "cl:disbandok")
    assert await clans.current(session_factory, OWNER) is None
    assert await clans.current(session_factory, ALICE) is None
    context.bot.send_message.assert_awaited_with(
        chat_id=ALICE, text="🦈 Shark Tank has been disbanded."
    )


# -- identity -----------------------------------------------------------------------------


async def test_photo_flow_stores_only_the_file_id(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    context = make_context(session_factory)
    await tap(context, OWNER, "cl:photo")

    update = make_message_update(OWNER)
    update.message.photo = (
        SimpleNamespace(file_id="small-fake-id"),
        SimpleNamespace(file_id="large-fake-id"),
    )
    await clan_hub.handle_clan_photo(update, context)

    assert (await clans.get_clan(session_factory, clan.id)).photo_file_id == "large-fake-id"
    update.message.reply_photo.assert_awaited()  # the profile now shows it
    assert update.message.reply_photo.call_args.kwargs["photo"] == "large-fake-id"


async def test_photos_outside_the_flow_are_ignored(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    update = make_message_update(OWNER)
    update.message.photo = (SimpleNamespace(file_id="stray-id"),)
    await clan_hub.handle_clan_photo(update, make_context(session_factory))
    assert (await clans.get_clan(session_factory, clan.id)).photo_file_id is None


async def test_motto_flow(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    context = make_context(session_factory)
    await tap(context, OWNER, "cl:motto")
    reply = make_message_update(OWNER, text="Fade the public")
    await router.handle_text(reply, context)
    assert (await clans.get_clan(session_factory, clan.id)).motto == "Fade the public"
    assert "“Fade the public”" in last_text(reply.message.reply_text)


async def test_settings_are_owner_only(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)
    query = await tap(make_context(session_factory), ALICE, "cl:settings")
    query.answer.assert_awaited_with(clans.NOT_OWNER_ERROR, show_alert=True)


# -- boards -------------------------------------------------------------------------------


async def test_clans_board_and_tab_switching(session_factory, onboard):
    await seed(session_factory, onboard)
    context = make_context(session_factory)
    update = make_message_update(OWNER, chat_type="supergroup", chat_id=HOME)
    await clan_hub.clans_command(update, context)

    text = last_text(update.message.reply_text)
    assert text.startswith("🏆 Clans · This week · % ROI")
    assert "🦈 Shark Tank: not ranked (needs 3 members)" in text
    tabs = [data for _, data in buttons(update.message.reply_text)]
    assert tabs == ["cb:d:roi", "cb:w:roi", "cb:a:roi", "cb:w:roi", "cb:w:usd"]

    query = await tap(context, OWNER, "cb:a:usd")
    assert last_text(query.edit_message_text).startswith("🏆 Clans · All time · $ PnL")


# -- group surface ------------------------------------------------------------------------


async def test_group_invite_posts_a_join_button_for_leaders_only(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    await clans.join(session_factory, ALICE, clan.invite_code)

    update = make_message_update(OWNER, chat_type="supergroup", chat_id=HOME)
    update.message.reply_to_message = SimpleNamespace(
        from_user=SimpleNamespace(id=CAROL, username="carol", first_name="C", is_bot=False)
    )
    await clan_hub.clan_command(update, make_context(session_factory, args=["invite"]))
    assert last_text(update.message.reply_text) == (
        "carol, you've been drafted into 🦈 Shark Tank. Tap to join."
    )
    assert buttons(update.message.reply_text) == [
        ("Join 🦈 Shark Tank", f"https://t.me/fadetest_bot?start=clan{clan.invite_code}")
    ]

    member = make_message_update(ALICE, chat_type="supergroup", chat_id=HOME)
    await clan_hub.clan_command(member, make_context(session_factory, args=["invite"]))
    assert last_text(member.message.reply_text) == clan_hub.GROUP_NEED_LEADER_TEXT


async def test_setup_offers_home_and_only_that_clans_leader_can_claim(session_factory, onboard):
    clan = await seed(session_factory, onboard)
    context = make_context(session_factory)
    update = make_message_update(OWNER, chat_type="supergroup", chat_id=HOME)
    await group_clan.setup(update, context)

    offer = update.message.reply_text.call_args_list[-1]
    assert "Make this chat the home of 🦈 Shark Tank?" in offer.args[0]

    outsider = await tap(context, CAROL, f"ch:{clan.id}", chat_id=HOME)
    outsider.answer.assert_awaited_with(clan_hub.HOME_WRONG_CLAN_ALERT, show_alert=True)

    query = await tap(context, OWNER, f"ch:{clan.id}", chat_id=HOME)
    assert "now home to 🦈 Shark Tank" in last_text(query.edit_message_text)
    assert (await clans.get_clan(session_factory, clan.id)).home_chat_id == HOME


async def test_group_clan_shows_the_home_clans_public_profile(session_factory, onboard):
    await seed(session_factory, onboard, home=HOME)
    update = make_message_update(ALICE, chat_type="supergroup", chat_id=HOME)
    await clan_hub.clan_command(update, make_context(session_factory))
    text = last_text(update.message.reply_text)
    assert text.startswith("🦈 Shark Tank")
    assert "Members: 1/50" in text
    assert "0x" not in text


async def test_answered_prompt_loses_its_stale_cancel_button(session_factory, onboard):
    """Once the name is accepted, the 'Name your clan' prompt above it must not
    keep a Cancel button that no longer cancels anything."""
    await onboard(ALICE)
    context = make_context(session_factory)
    context.bot.edit_message_reply_markup = AsyncMock()
    update, query = make_callback(ALICE, "cl:create")
    query.message.message_id = 4242
    await clan_hub.clan_callback(update, context)
    context.bot.edit_message_reply_markup.assert_not_awaited()  # still waiting

    # A rejected name keeps the flow (and its Cancel) open...
    await router.handle_text(make_message_update(ALICE, text="x"), context)
    context.bot.edit_message_reply_markup.assert_not_awaited()

    # ...an accepted one retires the prompt's keyboard.
    await router.handle_text(make_message_update(ALICE, text="Wolfpack"), context)
    context.bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=ALICE, message_id=4242, reply_markup=None
    )
    assert clan_hub.PROMPT_KEY not in context.user_data
