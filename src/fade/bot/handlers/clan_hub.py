"""/clan, /clans and the clan menus (D-15: clans are teams, not group chats).

Management happens in DM through one message that edits itself as you tap.
Every rule (permissions, caps, cooldowns) is enforced in fade.social.clans;
this module only renders and routes, so a missing check here can't let
anyone kick, rename, or disband.

Group surface: `/clan invite` posts a join button, `/clan` shows the home
clan's public profile, `/clans` shows the boards. Group messages never carry
balances or addresses — clan boards show realized PnL and ROI only, the same
exposure as the per-group /leaderboard.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from fade.bot.handlers.common import dm_button, edit_card, sanitize
from fade.bot.handlers.trade import user_onboarded
from fade.db.models import GroupChat
from fade.social import clans
from fade.social.broadcast import PendingMessage
from fade.social.leaderboard import EASTERN
from fade.social.league import (
    METRICS,
    WINDOWS,
    clan_label,
    compute_standings,
    describe_rank,
    render_board,
    window_start,
)

logger = logging.getLogger(__name__)

FLOW_KEY = "clan_flow"  # user_data: (flow, started_at)
PROMPT_KEY = "clan_prompt"  # (chat_id, message_id) of the prompt carrying Cancel
NEW_NAME_KEY = "clan_new_name"
INVITE_KEY = "clan_invite"  # a /start clan<code> waiting for onboarding to finish
FLOW_TTL_S = 600

NEED_ACCOUNT_TEXT = "You need an account first. DM me and run /start."
DM_ONLY_TEXT = "Clan management happens in DM. Tap below."
NO_CLAN_TEXT = (
    "You're not in a clan.\n\nStart one, or get an invite link from a clan's owner or an officer."
)
ASK_NAME_TEXT = (
    f"Name your clan ({clans.NAME_MIN}–{clans.NAME_MAX} characters: "
    "letters, numbers, spaces, . ' ! -).\n"
    "Choose wisely. Renames are once a week."
)
ASK_ICON_TEXT = "Pick an icon for {name}."
ASK_PHOTO_TEXT = "Send me a photo for your clan. Square works best."
ASK_MOTTO_TEXT = f"Send your clan's motto (up to {clans.MOTTO_MAX} characters)."
CREATED_TEXT = "{label} is born. Send your invite link to recruit; 3 members gets you ranked."
PHOTO_SET_TEXT = "Photo set."
MENU_CLOSED_TEXT = "Closed."
JOIN_PROMPT_TEXT = "Join {label}?\n{members}/{cap} members · Owner: {owner}"
JOIN_LEAVES_TEXT = "\nYou'll leave {label}."
JOINED_TEXT = "You're in {label}."
LEFT_TEXT = "You left {label}."
DISBANDED_TEXT = "{label} has been disbanded."
KICKED_DM_TEXT = "You were removed from {label} by its owner or an officer."
OWNER_DM_TEXT = "You're now the owner of {label}."
INVITE_TEXT = (
    "Invite link for {label}:\n{link}\n\n"
    "Anyone who opens it can join (up to {cap} members). Regenerating makes "
    "every old link stop working."
)
INVITE_UNAVAILABLE_TEXT = "Invite links need my username, and I can't see it right now. Try again."
GROUP_RECRUIT_TEXT = "{label} is recruiting. Tap to join."
GROUP_DRAFT_TEXT = "{who}, you've been drafted into {label}. Tap to join."
GROUP_NO_HOME_TEXT = (
    "This chat isn't home to a clan. Clan owners and officers: run /setup here to claim it."
)
GROUP_NEED_LEADER_TEXT = "Only a clan's owner or officers can post invites."
HOME_OFFER_TEXT = (
    "Make this chat the home of {label}? Members' trades and weekly results post here."
)
HOME_LINKED_TEXT = (
    "🏠 This chat is now home to {label}. Members' trades and weekly results post here."
)
HOME_WRONG_CLAN_ALERT = "That button belongs to another clan's leaders."
EVENT_JOINED_TEXT = "⚔️ {who} joined {label}."
EVENT_LEFT_TEXT = "{who} left {label}."
EVENT_KICKED_TEXT = "🥾 {who} was removed from {label}."
CONFIRM_KICK_TEXT = "Remove {who} from {label}? They can't rejoin for 24 hours."
CONFIRM_TRANSFER_TEXT = "Make {who} the owner of {label}? You'll become an officer."
CONFIRM_LEAVE_TEXT = "Leave {label}?"
CONFIRM_LEAVE_LAST_TEXT = "You're the only member. Leaving disbands {label}. Continue?"
CONFIRM_DISBAND_TEXT = (
    "Disband {label}? All {count} members are removed and the name is freed. This can't be undone."
)
STALE_ALERT = "That menu is out of date. Run /clan again."


# -- small helpers ----------------------------------------------------------------


def _sessions(context):
    return context.application.bot_data["sessions"]


def _label(clan) -> str:
    return clan_label(clan.icon, clan.name, limit=clans.NAME_MAX)


def _who(username: str | None, fallback) -> str:
    return sanitize(username or str(fallback), limit=40)


def _is_private(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.type == "private"


def _start_flow(context, flow: str, query) -> None:
    context.user_data[FLOW_KEY] = (flow, time.monotonic())
    # Remember the prompt so its Cancel button can be removed once answered.
    message = getattr(query, "message", None)
    if message is not None:
        context.user_data[PROMPT_KEY] = (message.chat.id, message.message_id)
    # The newest flow owns the next reply: a half-typed trade amount or
    # withdrawal must not swallow the clan name (or vice versa).
    context.user_data.pop("pending_custom", None)
    context.user_data.pop("withdraw", None)


async def _end_flow(context) -> None:
    """The flow got its answer: close it and strip the now-stale Cancel button
    off the prompt (it would otherwise linger above the result)."""
    context.user_data.pop(FLOW_KEY, None)
    where = context.user_data.pop(PROMPT_KEY, None)
    if not where:
        return
    chat_id, message_id = where
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
    except Exception:
        # Already edited or deleted: nothing left to tidy.
        logger.debug("clan prompt keyboard already gone")


def _take_flow(context) -> str | None:
    entry = context.user_data.get(FLOW_KEY)
    if not entry:
        return None
    flow, started = entry
    if time.monotonic() - started > FLOW_TTL_S:
        context.user_data.pop(FLOW_KEY, None)
        return None
    return flow


def _post_to_home(context, clan, text: str) -> None:
    queue = context.application.bot_data.get("broadcasts")
    if queue is not None and clan.home_chat_id is not None:
        queue.enqueue(PendingMessage(chat_id=clan.home_chat_id, text=text))


async def _dm(context, user_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
    except Exception:
        # They may have blocked the bot; the action itself already happened.
        logger.info("clan notice to %s not delivered", user_id)


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows]
    )


def _icon_keyboard() -> InlineKeyboardMarkup:
    icons = list(enumerate(clans.ICONS))
    rows = [
        [(icon, f"cl:icon:{index}") for index, icon in icons[start : start + 6]]
        for start in range(0, len(icons), 6)
    ]
    return _kb(rows + [[("Cancel", "cl:x")]])


def _board_keyboard(window: str, metric: str) -> InlineKeyboardMarkup:
    def mark(text, on):
        return f"• {text} •" if on else text

    return _kb(
        [
            [(mark(label, key == window), f"cb:{key}:{metric}") for key, label in WINDOWS.items()],
            [(mark(label, key == metric), f"cb:{window}:{key}") for key, label in METRICS.items()],
        ]
    )


# -- profile ------------------------------------------------------------------------


async def _profile(context, user_id: int):
    """(text, keyboard, photo_file_id) for the user's /clan screen."""
    sessions = _sessions(context)
    mine = await clans.current(sessions, user_id)
    if mine is None:
        return NO_CLAN_TEXT, _kb([[("⚔️ Create a clan", "cl:create")]]), None
    clan, stint = mine
    roster = await clans.members(sessions, clan.id)
    owner = next((m for m in roster if m.role == clans.OWNER), None)
    now = datetime.now(tz=EASTERN)
    week = await compute_standings(sessions, now, since=window_start("w", now))
    ever = await compute_standings(sessions, now)

    lines = [_label(clan)]
    if clan.motto:
        lines.append(f"“{sanitize(clan.motto, limit=clans.MOTTO_MAX)}”")
    lines += [
        "",
        f"Owner: {_who(owner.username if owner else None, clan.owner_id)}",
        f"Members: {len(roster)}/{clans.MEMBER_CAP} · You: {clans.ROLE_LABELS[stint.role]}",
    ]
    if clan.home_chat_id is not None:
        async with sessions() as session:
            group = await session.get(GroupChat, clan.home_chat_id)
        title = sanitize(group.title, limit=40) if group else "a group chat"
        lines.append(f"Home chat: {title}")
    lines += [
        "",
        "This week",
        f"  ROI: {describe_rank(week, clan.id, 'roi')}",
        f"  $: {describe_rank(week, clan.id, 'usd')}",
        "All time",
        f"  ROI: {describe_rank(ever, clan.id, 'roi')}",
        f"  $: {describe_rank(ever, clan.id, 'usd')}",
    ]
    leader = stint.role in (clans.OWNER, clans.OFFICER)
    rows = []
    rows.append([("🔗 Invite", "cl:invite"), ("👥 Members", "cl:members")] if leader
                else [("👥 Members", "cl:members")])  # fmt: skip
    rows.append([("🏆 Boards", "cb:w:roi")])
    if stint.role == clans.OWNER:
        rows.append([("⚙️ Settings", "cl:settings"), ("🚪 Leave", "cl:leave")])
    else:
        rows.append([("🚪 Leave", "cl:leave")])
    return "\n".join(lines), _kb(rows), clan.photo_file_id


async def _send_profile(message, context, user_id: int) -> None:
    text, keyboard, photo = await _profile(context, user_id)
    if photo:
        try:
            await message.reply_photo(photo=photo, caption=text, reply_markup=keyboard)
            return
        except Exception:
            logger.warning("clan photo failed to send; falling back to text")
    await message.reply_text(text, reply_markup=keyboard)


async def _show_profile(query, context, user_id: int) -> None:
    text, keyboard, _ = await _profile(context, user_id)
    await edit_card(query, text, keyboard)


# -- commands -----------------------------------------------------------------------


async def clan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clan: your clan's profile + menu (DM); invites and public profile (group)."""
    if update.message is None or update.effective_user is None:
        return
    if not _is_private(update):
        await _clan_in_group(update, context)
        return
    if not await user_onboarded(context, update.effective_user.id, update.effective_user):
        await update.message.reply_text(NEED_ACCOUNT_TEXT)
        return
    await _send_profile(update.message, context, update.effective_user.id)


async def _clan_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sessions = _sessions(context)
    args = [a.lower() for a in (context.args or []) if isinstance(a, str)]
    if args[:1] == ["invite"]:
        mine = await clans.current(sessions, update.effective_user.id)
        if mine is None or mine[1].role not in (clans.OWNER, clans.OFFICER):
            await update.message.reply_text(GROUP_NEED_LEADER_TEXT)
            return
        clan = mine[0]
        if not context.bot.username:
            await update.message.reply_text(INVITE_UNAVAILABLE_TEXT)
            return
        link = clans.invite_link(context.bot.username, clan.invite_code)
        target = getattr(getattr(update.message, "reply_to_message", None), "from_user", None)
        if target is not None and getattr(target, "is_bot", False) is False:
            who = _who(getattr(target, "username", None) or getattr(target, "first_name", None),
                       target.id)  # fmt: skip
            text = GROUP_DRAFT_TEXT.format(who=who, label=_label(clan))
        else:
            text = GROUP_RECRUIT_TEXT.format(label=_label(clan))
        button = InlineKeyboardButton(f"Join {_label(clan)}", url=link)
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup([[button]]))
        return

    clan = await clans.by_home_chat(sessions, update.effective_chat.id)
    if clan is None:
        await update.message.reply_text(GROUP_NO_HOME_TEXT)
        return
    roster = await clans.members(sessions, clan.id)
    now = datetime.now(tz=EASTERN)
    week = await compute_standings(sessions, now, since=window_start("w", now))
    lines = [_label(clan)]
    if clan.motto:
        lines.append(f"“{sanitize(clan.motto, limit=clans.MOTTO_MAX)}”")
    lines += [
        f"Members: {len(roster)}/{clans.MEMBER_CAP}",
        f"This week · ROI: {describe_rank(week, clan.id, 'roi')}",
        f"This week · $: {describe_rank(week, clan.id, 'usd')}",
    ]
    markup = dm_button(context.bot.username, "clan") if context.bot.username else None
    await update.message.reply_text("\n".join(lines), reply_markup=markup)


async def clans_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clans: clan leaderboards — today / week / all time × % ROI / $ PnL."""
    if update.message is None:
        return
    text = await _board_text(context, "w", "roi", update.effective_user)
    await update.message.reply_text(text, reply_markup=_board_keyboard("w", "roi"))


async def _board_text(context, window: str, metric: str, tg_user) -> str:
    sessions = _sessions(context)
    now = datetime.now(tz=EASTERN)
    standings = await compute_standings(sessions, now, since=window_start(window, now))
    mine = await clans.current(sessions, tg_user.id) if tg_user is not None else None
    return render_board(standings, window, metric, mine[0].id if mine else None)


async def board_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    _, window, metric = query.data.split(":")
    if window not in WINDOWS or metric not in METRICS:
        await query.answer()
        return
    await query.answer()
    text = await _board_text(context, window, metric, update.effective_user)
    try:
        await edit_card(query, text, _board_keyboard(window, metric))
    except Exception as error:
        # Re-tapping the selected tab edits to identical content; Telegram
        # rejects that as "message is not modified". Nothing to do.
        if "not modified" not in str(error).lower():
            raise


# -- deep-link joins ------------------------------------------------------------------


def clan_code_from(payload) -> str | None:
    """Parse a `clan<code>` /start payload."""
    if not isinstance(payload, str) or not payload.lower().startswith("clan"):
        return None
    code = payload[4:].lower()
    return code if code.isalnum() and 4 <= len(code) <= 16 else None


async def offer_join(message, context, user_id: int, code: str) -> None:
    """The plain confirm screen behind an invite link."""
    sessions = _sessions(context)
    clan = await clans.by_invite_code(sessions, code)
    if clan is None:
        await message.reply_text(clans.BAD_INVITE_ERROR)
        return
    mine = await clans.current(sessions, user_id)
    if mine is not None and mine[0].id == clan.id:
        await message.reply_text(clans.ALREADY_MEMBER_ERROR)
        return
    roster = await clans.members(sessions, clan.id)
    owner = next((m for m in roster if m.role == clans.OWNER), None)
    text = JOIN_PROMPT_TEXT.format(
        label=_label(clan),
        members=len(roster),
        cap=clans.MEMBER_CAP,
        owner=_who(owner.username if owner else None, clan.owner_id),
    )
    if mine is not None:
        text += JOIN_LEAVES_TEXT.format(label=_label(mine[0]))
    await message.reply_text(
        text, reply_markup=_kb([[("Join", f"cl:joinok:{code}"), ("Cancel", "cl:x")]])
    )


async def resume_pending_invite(message, context, user_id: int) -> None:
    """Called once onboarding finishes: a new user who arrived through an
    invite link lands on that clan's join screen instead of losing it."""
    code = context.user_data.pop(INVITE_KEY, None)
    if isinstance(code, str) and code:
        await offer_join(message, context, user_id, code)


# -- the menu callback ----------------------------------------------------------------


async def clan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    parts = query.data.split(":")
    action, arg = parts[1], (parts[2] if len(parts) > 2 else None)
    user_id = update.effective_user.id
    sessions = _sessions(context)

    if action == "x":
        context.user_data.pop(FLOW_KEY, None)
        context.user_data.pop(PROMPT_KEY, None)  # this edit replaces the prompt
        context.user_data.pop(NEW_NAME_KEY, None)
        await query.answer()
        await edit_card(query, MENU_CLOSED_TEXT)
        return
    if not await user_onboarded(context, user_id, update.effective_user):
        await query.answer(NEED_ACCOUNT_TEXT, show_alert=True)
        return

    handler = _ACTIONS.get(action)
    if handler is None:
        await query.answer(STALE_ALERT, show_alert=True)
        return
    try:
        await handler(query, context, sessions, user_id, arg)
    except clans.ClanError as error:
        await query.answer(str(error), show_alert=True)


async def _menu(query, context, sessions, user_id, arg):
    await query.answer()
    await _show_profile(query, context, user_id)


async def _create(query, context, sessions, user_id, arg):
    if await clans.current(sessions, user_id) is not None:
        raise clans.ClanError(clans.ALREADY_IN_CLAN_ERROR)
    await query.answer()
    _start_flow(context, "create_name", query)
    await edit_card(query, ASK_NAME_TEXT, _kb([[("Cancel", "cl:x")]]))


async def _icon(query, context, sessions, user_id, arg):
    try:
        icon = clans.ICONS[int(arg)]
    except (TypeError, ValueError, IndexError):
        raise clans.ClanError(clans.BAD_ICON_ERROR) from None
    new_name = context.user_data.get(NEW_NAME_KEY)
    if new_name:
        clan = await clans.create(sessions, user_id, new_name, icon)
        context.user_data.pop(NEW_NAME_KEY, None)
        await query.answer()
        text, keyboard, _ = await _profile(context, user_id)
        await edit_card(query, CREATED_TEXT.format(label=_label(clan)) + "\n\n" + text, keyboard)
        return
    await clans.set_icon(sessions, user_id, icon)
    await query.answer("Icon updated.")
    await _show_profile(query, context, user_id)


async def _icons(query, context, sessions, user_id, arg):
    mine = await _require_clan(sessions, user_id)
    if mine[1].role != clans.OWNER:
        raise clans.ClanError(clans.NOT_OWNER_ERROR)
    await query.answer()
    await edit_card(query, ASK_ICON_TEXT.format(name=sanitize(mine[0].name)), _icon_keyboard())


async def _invite(query, context, sessions, user_id, arg):
    clan, stint = await _require_clan(sessions, user_id)
    if stint.role not in (clans.OWNER, clans.OFFICER):
        raise clans.ClanError(clans.NOT_LEADER_ERROR)
    await query.answer()
    await _render_invite(query, context, clan, clan.invite_code)


async def _regen(query, context, sessions, user_id, arg):
    code = await clans.regenerate_invite(sessions, user_id)
    clan, _ = await _require_clan(sessions, user_id)
    await query.answer("New link made. Old links are dead.")
    await _render_invite(query, context, clan, code)


async def _render_invite(query, context, clan, code: str) -> None:
    if not context.bot.username:
        await edit_card(query, INVITE_UNAVAILABLE_TEXT, _kb([[("Back", "cl:menu")]]))
        return
    text = INVITE_TEXT.format(
        label=_label(clan), link=clans.invite_link(context.bot.username, code), cap=clans.MEMBER_CAP
    )
    await edit_card(query, text, _kb([[("🔄 Regenerate", "cl:regen"), ("Back", "cl:menu")]]))


async def _members(query, context, sessions, user_id, arg):
    clan, stint = await _require_clan(sessions, user_id)
    roster = await clans.members(sessions, clan.id)
    badge = {clans.OWNER: "👑", clans.OFFICER: "⭐", clans.MEMBER: "•"}
    rows = [
        [(f"{badge.get(m.role, '•')} {_who(m.username, m.telegram_user_id)}",
          f"cl:m:{m.telegram_user_id}")]
        for m in roster
    ]  # fmt: skip
    rows.append([("Back", "cl:menu")])
    await query.answer()
    text = f"{_label(clan)} · {len(roster)}/{clans.MEMBER_CAP} members\n👑 owner · ⭐ officer"
    await edit_card(query, text, _kb(rows))


async def _member(query, context, sessions, user_id, arg):
    clan, stint = await _require_clan(sessions, user_id)
    target = await _roster_entry(sessions, clan.id, arg)
    rows = []
    if target.telegram_user_id != user_id:
        if stint.role == clans.OWNER:
            if target.role == clans.MEMBER:
                rows.append([("⭐ Make officer", f"cl:promote:{target.telegram_user_id}")])
            else:
                rows.append([("Make member", f"cl:demote:{target.telegram_user_id}")])
            rows.append([("👑 Make owner", f"cl:xfer:{target.telegram_user_id}")])
        if stint.role == clans.OWNER or (
            stint.role == clans.OFFICER and target.role == clans.MEMBER
        ):
            rows.append([("🥾 Kick", f"cl:kick:{target.telegram_user_id}")])
    rows.append([("Back", "cl:members")])
    joined = target.joined_at.strftime("%b %d, %Y").replace(" 0", " ")
    text = (
        f"{_who(target.username, target.telegram_user_id)}\n"
        f"{clans.ROLE_LABELS[target.role]} of {_label(clan)} since {joined}"
    )
    await query.answer()
    await edit_card(query, text, _kb(rows))


async def _promote(query, context, sessions, user_id, arg):
    await clans.set_role(sessions, user_id, int(arg), clans.OFFICER)
    await query.answer("Promoted to officer.")
    await _member(query, context, sessions, user_id, arg)


async def _demote(query, context, sessions, user_id, arg):
    await clans.set_role(sessions, user_id, int(arg), clans.MEMBER)
    await query.answer("Now a member.")
    await _member(query, context, sessions, user_id, arg)


async def _kick(query, context, sessions, user_id, arg):
    clan, _ = await _require_clan(sessions, user_id)
    target = await _roster_entry(sessions, clan.id, arg)
    await query.answer()
    text = CONFIRM_KICK_TEXT.format(
        who=_who(target.username, target.telegram_user_id), label=_label(clan)
    )
    await edit_card(query, text, _kb([[("Remove", f"cl:kickok:{arg}"), ("Cancel", f"cl:m:{arg}")]]))


async def _kick_ok(query, context, sessions, user_id, arg):
    clan, _ = await _require_clan(sessions, user_id)
    gone = await clans.kick(sessions, user_id, int(arg))
    await query.answer("Removed.")
    who = _who(gone.username, gone.telegram_user_id)
    _post_to_home(context, clan, EVENT_KICKED_TEXT.format(who=who, label=_label(clan)))
    await _dm(context, gone.telegram_user_id, KICKED_DM_TEXT.format(label=_label(clan)))
    await _members(query, context, sessions, user_id, None)


async def _xfer(query, context, sessions, user_id, arg):
    clan, _ = await _require_clan(sessions, user_id)
    target = await _roster_entry(sessions, clan.id, arg)
    await query.answer()
    text = CONFIRM_TRANSFER_TEXT.format(
        who=_who(target.username, target.telegram_user_id), label=_label(clan)
    )
    keyboard = _kb([[("Confirm", f"cl:xferok:{arg}"), ("Cancel", f"cl:m:{arg}")]])
    await edit_card(query, text, keyboard)


async def _xfer_ok(query, context, sessions, user_id, arg):
    clan, _ = await _require_clan(sessions, user_id)
    await clans.transfer(sessions, user_id, int(arg))
    await query.answer("Ownership transferred.")
    await _dm(context, int(arg), OWNER_DM_TEXT.format(label=_label(clan)))
    await _show_profile(query, context, user_id)


async def _settings(query, context, sessions, user_id, arg):
    clan, stint = await _require_clan(sessions, user_id)
    if stint.role != clans.OWNER:
        raise clans.ClanError(clans.NOT_OWNER_ERROR)
    rows = [
        [("✏️ Rename", "cl:rename"), ("🎭 Icon", "cl:icons")],
        [("🖼 Photo", "cl:photo"), ("💬 Motto", "cl:motto")],
    ]
    if clan.photo_file_id:
        rows.append([("Remove photo", "cl:nophoto")])
    if clan.motto:
        rows.append([("Clear motto", "cl:nomotto")])
    if clan.home_chat_id is not None:
        rows.append([("Unlink home chat", "cl:unlink")])
    rows.append([("💥 Disband", "cl:disband"), ("Back", "cl:menu")])
    await query.answer()
    await edit_card(query, f"{_label(clan)} · settings", _kb(rows))


async def _rename(query, context, sessions, user_id, arg):
    _, stint = await _require_clan(sessions, user_id)
    if stint.role != clans.OWNER:
        raise clans.ClanError(clans.NOT_OWNER_ERROR)
    await query.answer()
    _start_flow(context, "rename", query)
    await edit_card(query, ASK_NAME_TEXT, _kb([[("Cancel", "cl:x")]]))


async def _photo(query, context, sessions, user_id, arg):
    _, stint = await _require_clan(sessions, user_id)
    if stint.role != clans.OWNER:
        raise clans.ClanError(clans.NOT_OWNER_ERROR)
    await query.answer()
    _start_flow(context, "photo", query)
    await edit_card(query, ASK_PHOTO_TEXT, _kb([[("Cancel", "cl:x")]]))


async def _no_photo(query, context, sessions, user_id, arg):
    await clans.set_photo(sessions, user_id, None)
    await query.answer("Photo removed.")
    await _settings(query, context, sessions, user_id, None)


async def _motto(query, context, sessions, user_id, arg):
    _, stint = await _require_clan(sessions, user_id)
    if stint.role != clans.OWNER:
        raise clans.ClanError(clans.NOT_OWNER_ERROR)
    await query.answer()
    _start_flow(context, "motto", query)
    await edit_card(query, ASK_MOTTO_TEXT, _kb([[("Cancel", "cl:x")]]))


async def _no_motto(query, context, sessions, user_id, arg):
    await clans.set_motto(sessions, user_id, None)
    await query.answer("Motto cleared.")
    await _settings(query, context, sessions, user_id, None)


async def _unlink(query, context, sessions, user_id, arg):
    await clans.unlink_home_chat(sessions, user_id)
    await query.answer("Home chat unlinked.")
    await _settings(query, context, sessions, user_id, None)


async def _leave(query, context, sessions, user_id, arg):
    clan, stint = await _require_clan(sessions, user_id)
    count = await clans.member_count(sessions, clan.id)
    if stint.role == clans.OWNER and count > 1:
        raise clans.ClanError(clans.OWNER_MUST_TRANSFER_ERROR)
    template = CONFIRM_LEAVE_LAST_TEXT if stint.role == clans.OWNER else CONFIRM_LEAVE_TEXT
    await query.answer()
    await edit_card(
        query,
        template.format(label=_label(clan)),
        _kb([[("Leave", "cl:leaveok"), ("Cancel", "cl:menu")]]),
    )


async def _leave_ok(query, context, sessions, user_id, arg):
    clan = await clans.leave(sessions, user_id)
    await query.answer()
    if clan.disbanded_at is not None:
        await edit_card(query, DISBANDED_TEXT.format(label=_label(clan)))
        return
    who = _who(query.from_user.username if query.from_user else None, user_id)
    _post_to_home(context, clan, EVENT_LEFT_TEXT.format(who=who, label=_label(clan)))
    await edit_card(query, LEFT_TEXT.format(label=_label(clan)))


async def _disband(query, context, sessions, user_id, arg):
    clan, stint = await _require_clan(sessions, user_id)
    if stint.role != clans.OWNER:
        raise clans.ClanError(clans.NOT_OWNER_ERROR)
    count = await clans.member_count(sessions, clan.id)
    await query.answer()
    await edit_card(
        query,
        CONFIRM_DISBAND_TEXT.format(label=_label(clan), count=count),
        _kb([[("Disband", "cl:disbandok"), ("Cancel", "cl:settings")]]),
    )


async def _disband_ok(query, context, sessions, user_id, arg):
    roster = []
    mine = await clans.current(sessions, user_id)
    if mine is not None:
        roster = await clans.members(sessions, mine[0].id)
    home_chat = mine[0].home_chat_id if mine else None
    clan = await clans.disband(sessions, user_id)
    await query.answer()
    text = DISBANDED_TEXT.format(label=_label(clan))
    for member in roster:
        if member.telegram_user_id != user_id:
            await _dm(context, member.telegram_user_id, text)
    queue = context.application.bot_data.get("broadcasts")
    if queue is not None and home_chat is not None:
        queue.enqueue(PendingMessage(chat_id=home_chat, text=text))
    await edit_card(query, text)


async def _join_ok(query, context, sessions, user_id, arg):
    clan = await clans.join(sessions, user_id, arg or "")
    await query.answer()
    who = _who(query.from_user.username if query.from_user else None, user_id)
    _post_to_home(context, clan, EVENT_JOINED_TEXT.format(who=who, label=_label(clan)))
    await edit_card(
        query, JOINED_TEXT.format(label=_label(clan)), _kb([[("Open clan", "cl:menu")]])
    )


_ACTIONS = {
    "menu": _menu,
    "create": _create,
    "icon": _icon,
    "icons": _icons,
    "invite": _invite,
    "regen": _regen,
    "members": _members,
    "m": _member,
    "promote": _promote,
    "demote": _demote,
    "kick": _kick,
    "kickok": _kick_ok,
    "xfer": _xfer,
    "xferok": _xfer_ok,
    "settings": _settings,
    "rename": _rename,
    "photo": _photo,
    "nophoto": _no_photo,
    "motto": _motto,
    "nomotto": _no_motto,
    "unlink": _unlink,
    "leave": _leave,
    "leaveok": _leave_ok,
    "disband": _disband,
    "disbandok": _disband_ok,
    "joinok": _join_ok,
}


async def _require_clan(sessions, user_id: int):
    mine = await clans.current(sessions, user_id)
    if mine is None:
        raise clans.ClanError(clans.NOT_IN_CLAN_ERROR)
    return mine


async def _roster_entry(sessions, clan_id: int, arg) -> clans.MemberView:
    try:
        target_id = int(arg)
    except (TypeError, ValueError):
        raise clans.ClanError(clans.TARGET_NOT_MEMBER_ERROR) from None
    for member in await clans.members(sessions, clan_id):
        if member.telegram_user_id == target_id:
            return member
    raise clans.ClanError(clans.TARGET_NOT_MEMBER_ERROR)


# -- free-text and photo replies (DM) --------------------------------------------------


async def handle_clan_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Consume a pending clan name/motto reply. True if it handled the text."""
    flow = _take_flow(context)
    if flow not in ("create_name", "rename", "motto") or update.message is None:
        return False
    if update.effective_user is None:
        return False
    sessions = _sessions(context)
    user_id = update.effective_user.id
    raw = update.message.text or ""
    try:
        if flow == "create_name":
            name = clans.clean_name(raw)
            await clans.ensure_name_available(sessions, name)
            await _end_flow(context)
            context.user_data[NEW_NAME_KEY] = name
            await update.message.reply_text(
                ASK_ICON_TEXT.format(name=name), reply_markup=_icon_keyboard()
            )
            return True
        if flow == "rename":
            await clans.rename(sessions, user_id, raw)
        else:
            await clans.set_motto(sessions, user_id, raw)
    except clans.ClanError as error:
        # Stay in the flow: the next message is another attempt.
        await update.message.reply_text(str(error))
        return True
    await _end_flow(context)
    await _send_profile(update.message, context, user_id)
    return True


async def handle_clan_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A photo sent in DM while the clan photo flow is open."""
    if _take_flow(context) != "photo" or update.message is None or not update.message.photo:
        return
    if update.effective_user is None:
        return
    # Telegram sends several sizes; the last is the largest. Only the file_id
    # is stored — a reference Telegram resolves, never the image itself.
    file_id = update.message.photo[-1].file_id
    try:
        await clans.set_photo(_sessions(context), update.effective_user.id, file_id)
    except clans.ClanError as error:
        await update.message.reply_text(str(error))
        return
    await _end_flow(context)
    await update.message.reply_text(PHOTO_SET_TEXT)
    await _send_profile(update.message, context, update.effective_user.id)


# -- home chat (from /setup in a group) ------------------------------------------------


async def offer_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """After /setup: a clan leader whose clan has no home gets a one-tap offer."""
    if update.effective_user is None or update.message is None:
        return
    sessions = _sessions(context)
    mine = await clans.current(sessions, update.effective_user.id)
    if mine is None or mine[1].role not in (clans.OWNER, clans.OFFICER):
        return
    clan = mine[0]
    if clan.home_chat_id is not None:
        return
    if await clans.by_home_chat(sessions, update.effective_chat.id) is not None:
        return
    await update.message.reply_text(
        HOME_OFFER_TEXT.format(label=_label(clan)),
        reply_markup=_kb([[("🏠 Make it home", f"ch:{clan.id}")]]),
    )


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    clan_id = int(query.data.split(":")[1])
    sessions = _sessions(context)
    mine = await clans.current(sessions, update.effective_user.id)
    if mine is None or mine[0].id != clan_id:
        await query.answer(HOME_WRONG_CLAN_ALERT, show_alert=True)
        return
    try:
        clan = await clans.link_home_chat(sessions, update.effective_user.id, query.message.chat.id)
    except clans.ClanError as error:
        await query.answer(str(error), show_alert=True)
        return
    await query.answer()
    await edit_card(query, HOME_LINKED_TEXT.format(label=_label(clan)))
