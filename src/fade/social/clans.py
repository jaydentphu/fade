"""Clans as teams (D-15): every rule lives here, handlers only render.

A clan is a named team with an icon, an owner, officers, and members. Each
user is in at most one clan at a time. Memberships are kept as stints
([joined_at, left_at)) so PnL stays credited to the clan a trade closed in.

Every mutating function takes the acting user's id and enforces permissions
itself — a handler forgetting a check must not be able to kick someone.
Failures raise ClanError with user-facing copy (plain, not mean: these are
membership screens people need to understand).
"""

from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from fade.db.models import Clan, ClanMembership, GroupMember, User
from fade.social.leaderboard import to_naive_utc

NAME_MIN, NAME_MAX = 3, 24
MOTTO_MAX = 80
MEMBER_CAP = 50
RENAME_COOLDOWN = timedelta(days=7)
# Rejoin block for the clan that kicked you; and how often you can switch
# clans (anti-hopping: PnL is credited to the clan you're in when it closes).
KICK_COOLDOWN = timedelta(hours=24)
SWITCH_COOLDOWN = timedelta(hours=24)
INVITE_CODE_LEN = 10
# Lowercase only: /start payloads are lowercased before routing.
_CODE_ALPHABET = string.ascii_lowercase + string.digits

OWNER, OFFICER, MEMBER = "owner", "officer", "member"
ROLE_ORDER = {OWNER: 0, OFFICER: 1, MEMBER: 2}
ROLE_LABELS = {OWNER: "Owner", OFFICER: "Officer", MEMBER: "Member"}

ICONS = (
    "⚔️", "🐺", "🦈", "🔥", "💀", "👑", "🐂", "🐻",
    "🎯", "🧠", "🦅", "🐍", "🚀", "💎", "🃏", "🎲",
    "🦍", "🐉", "⚡", "🌊", "🏴‍☠️", "🥷", "🧨", "🍀",
)  # fmt: skip
DEFAULT_ICON = ICONS[0]

# Letters, digits, spaces and a little punctuation. No underscores, brackets,
# asterisks or backticks: sanitize() strips those, so a name containing them
# would render differently from what the owner typed.
_NAME_RE = re.compile(r"^[A-Za-z0-9 .'!-]+$")

# -- user-facing failure copy ---------------------------------------------------

NAME_LENGTH_ERROR = f"Clan names are {NAME_MIN}–{NAME_MAX} characters."
NAME_CHARS_ERROR = "Clan names can use letters, numbers, spaces, and . ' ! - only."
NAME_TAKEN_ERROR = "That name is taken. Pick another."
MOTTO_LENGTH_ERROR = f"Mottos are {MOTTO_MAX} characters max."
ALREADY_IN_CLAN_ERROR = "You're already in a clan. Leave it first."
NOT_IN_CLAN_ERROR = "You're not in a clan."
NOT_OWNER_ERROR = "Only the clan owner can do that."
NOT_LEADER_ERROR = "Only the owner or an officer can do that."
BAD_INVITE_ERROR = "That invite link is no longer valid. Ask for a fresh one."
CLAN_FULL_ERROR = f"That clan is full ({MEMBER_CAP} members)."
ALREADY_MEMBER_ERROR = "You're already in this clan."
KICKED_RECENTLY_ERROR = "You were removed from this clan recently. Try again in 24 hours."
SWITCH_TOO_SOON_ERROR = "You can switch clans once every 24 hours. Try again later."
TARGET_NOT_MEMBER_ERROR = "That person isn't in your clan."
CANT_TARGET_SELF_ERROR = "You can't do that to yourself."
OUTRANKED_ERROR = "Officers can only remove members."
OWNER_MUST_TRANSFER_ERROR = "You own this clan. Make someone else owner first, or disband it."
RENAME_TOO_SOON_ERROR = "Clans can be renamed once every 7 days."
BAD_ICON_ERROR = "Pick an icon from the list."
HOME_TAKEN_ERROR = "That chat is already home to another clan."


class ClanError(Exception):
    """A rule said no. str(error) is safe to show the user."""


@dataclass(frozen=True)
class MemberView:
    telegram_user_id: int
    username: str
    role: str
    joined_at: datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# -- validation -------------------------------------------------------------------


def clean_name(raw: str) -> str:
    """Validate and normalize a clan name (collapsed spaces). Raises ClanError."""
    name = " ".join((raw or "").split())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise ClanError(NAME_LENGTH_ERROR)
    if not _NAME_RE.match(name) or not any(ch.isalnum() for ch in name):
        raise ClanError(NAME_CHARS_ERROR)
    return name


def name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def clean_motto(raw: str) -> str:
    motto = " ".join((raw or "").split())
    if len(motto) > MOTTO_MAX:
        raise ClanError(MOTTO_LENGTH_ERROR)
    return motto


def new_invite_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(INVITE_CODE_LEN))


def invite_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{bot_username}?start=clan{code}"


# -- reads --------------------------------------------------------------------------


async def current(session_factory, telegram_user_id: int) -> tuple[Clan, ClanMembership] | None:
    """The user's live clan and their open stint in it, or None."""
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Clan, ClanMembership)
                .join(ClanMembership, ClanMembership.clan_id == Clan.id)
                .where(
                    ClanMembership.telegram_user_id == telegram_user_id,
                    ClanMembership.left_at.is_(None),
                    Clan.disbanded_at.is_(None),
                )
            )
        ).first()
    return (row[0], row[1]) if row else None


async def get_clan(session_factory, clan_id: int) -> Clan | None:
    async with session_factory() as session:
        clan = await session.get(Clan, clan_id)
    return clan if clan is not None and clan.disbanded_at is None else None


async def by_invite_code(session_factory, code: str) -> Clan | None:
    async with session_factory() as session:
        return await session.scalar(
            select(Clan).where(Clan.invite_code == code.lower(), Clan.disbanded_at.is_(None))
        )


async def by_home_chat(session_factory, chat_id: int) -> Clan | None:
    async with session_factory() as session:
        return await session.scalar(
            select(Clan).where(Clan.home_chat_id == chat_id, Clan.disbanded_at.is_(None))
        )


async def ensure_name_available(session_factory, name: str) -> None:
    """Raise NAME_TAKEN_ERROR early (create re-checks; this is for the prompt)."""
    async with session_factory() as session:
        await _ensure_name_free(session, name)


async def member_count(session_factory, clan_id: int) -> int:
    async with session_factory() as session:
        return await _open_count(session, clan_id)


async def members(session_factory, clan_id: int) -> list[MemberView]:
    """Open members, owner first, then officers, then by join time."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ClanMembership, User.username)
                .join(User, User.telegram_user_id == ClanMembership.telegram_user_id)
                .where(ClanMembership.clan_id == clan_id, ClanMembership.left_at.is_(None))
            )
        ).all()
    views = [
        MemberView(
            telegram_user_id=m.telegram_user_id,
            username=username or str(m.telegram_user_id),
            role=m.role,
            joined_at=to_naive_utc(m.joined_at),
        )
        for m, username in rows
    ]
    views.sort(key=lambda v: (ROLE_ORDER.get(v.role, 9), v.joined_at))
    return views


# -- writes -------------------------------------------------------------------------


async def create(
    session_factory, owner_id: int, name: str, icon: str = DEFAULT_ICON, *, now=None
) -> Clan:
    now = now or utcnow()
    name = clean_name(name)
    if icon not in ICONS:
        raise ClanError(BAD_ICON_ERROR)
    async with session_factory() as session:
        if await _open_stint(session, owner_id) is not None:
            raise ClanError(ALREADY_IN_CLAN_ERROR)
        await _ensure_name_free(session, name)
        clan = Clan(
            name=name,
            name_key=name_key(name),
            icon=icon,
            owner_id=owner_id,
            invite_code=await _unique_code(session),
        )
        session.add(clan)
        await session.flush()
        session.add(
            ClanMembership(clan_id=clan.id, telegram_user_id=owner_id, role=OWNER, joined_at=now)
        )
        await session.commit()
        return clan


async def join(session_factory, telegram_user_id: int, code: str, *, now=None) -> Clan:
    """Join via invite code; leaves the user's current clan (if allowed)."""
    now = now or utcnow()
    async with session_factory() as session:
        clan = await session.scalar(
            select(Clan).where(Clan.invite_code == code.lower(), Clan.disbanded_at.is_(None))
        )
        if clan is None:
            raise ClanError(BAD_INVITE_ERROR)
        stint = await _open_stint(session, telegram_user_id)
        if stint is not None and stint.clan_id == clan.id:
            raise ClanError(ALREADY_MEMBER_ERROR)
        if stint is not None and stint.role == OWNER:
            raise ClanError(OWNER_MUST_TRANSFER_ERROR)
        if await _open_count(session, clan.id) >= MEMBER_CAP:
            raise ClanError(CLAN_FULL_ERROR)
        if await _kicked_recently(session, telegram_user_id, clan.id, now):
            raise ClanError(KICKED_RECENTLY_ERROR)
        if await _switched_recently(session, telegram_user_id, now):
            raise ClanError(SWITCH_TOO_SOON_ERROR)
        if stint is not None:
            await _close(session, stint, now, "left")
        session.add(
            ClanMembership(
                clan_id=clan.id, telegram_user_id=telegram_user_id, role=MEMBER, joined_at=now
            )
        )
        if clan.home_chat_id is not None:
            # Members' trades broadcast into the clan's home chat (F-17 path).
            home = clan.home_chat_id
            if await session.get(GroupMember, (home, telegram_user_id)) is None:
                session.add(GroupMember(chat_id=home, telegram_user_id=telegram_user_id))
        await session.commit()
        return clan


async def leave(session_factory, telegram_user_id: int, *, now=None) -> Clan:
    """Leave your clan. An owner must transfer first unless they're alone,
    in which case leaving disbands the clan."""
    now = now or utcnow()
    async with session_factory() as session:
        stint = await _open_stint(session, telegram_user_id)
        if stint is None:
            raise ClanError(NOT_IN_CLAN_ERROR)
        clan = await session.get(Clan, stint.clan_id)
        if stint.role == OWNER:
            if await _open_count(session, clan.id) > 1:
                raise ClanError(OWNER_MUST_TRANSFER_ERROR)
            await _close(session, stint, now, "disbanded")
            _disband_row(clan, now)
        else:
            await _close(session, stint, now, "left")
            await _drop_home_broadcasts(session, clan, telegram_user_id)
        await session.commit()
        return clan


async def kick(session_factory, actor_id: int, target_id: int, *, now=None) -> MemberView:
    now = now or utcnow()
    if actor_id == target_id:
        raise ClanError(CANT_TARGET_SELF_ERROR)
    async with session_factory() as session:
        actor, target = await _actor_and_target(session, actor_id, target_id)
        if actor.role not in (OWNER, OFFICER):
            raise ClanError(NOT_LEADER_ERROR)
        if actor.role == OFFICER and target.role != MEMBER:
            raise ClanError(OUTRANKED_ERROR)
        await _close(session, target, now, "kicked")
        clan = await session.get(Clan, actor.clan_id)
        await _drop_home_broadcasts(session, clan, target_id)
        username = await session.scalar(
            select(User.username).where(User.telegram_user_id == target_id)
        )
        await session.commit()
        return MemberView(target_id, username or str(target_id), target.role, now)


async def set_role(session_factory, actor_id: int, target_id: int, role: str) -> None:
    """Owner promotes a member to officer or demotes an officer to member."""
    if role not in (OFFICER, MEMBER):
        raise ValueError(role)
    if actor_id == target_id:
        raise ClanError(CANT_TARGET_SELF_ERROR)
    async with session_factory() as session:
        actor, target = await _actor_and_target(session, actor_id, target_id)
        if actor.role != OWNER:
            raise ClanError(NOT_OWNER_ERROR)
        target.role = role
        await session.commit()


async def transfer(session_factory, actor_id: int, target_id: int) -> None:
    """Owner hands the clan to a member; the old owner becomes an officer."""
    if actor_id == target_id:
        raise ClanError(CANT_TARGET_SELF_ERROR)
    async with session_factory() as session:
        actor, target = await _actor_and_target(session, actor_id, target_id)
        if actor.role != OWNER:
            raise ClanError(NOT_OWNER_ERROR)
        clan = await session.get(Clan, actor.clan_id)
        actor.role, target.role = OFFICER, OWNER
        clan.owner_id = target_id
        await session.commit()


async def disband(session_factory, actor_id: int, *, now=None) -> Clan:
    now = now or utcnow()
    async with session_factory() as session:
        stint = await _open_stint(session, actor_id)
        if stint is None:
            raise ClanError(NOT_IN_CLAN_ERROR)
        if stint.role != OWNER:
            raise ClanError(NOT_OWNER_ERROR)
        clan = await session.get(Clan, stint.clan_id)
        for member in await session.scalars(
            select(ClanMembership).where(
                ClanMembership.clan_id == clan.id, ClanMembership.left_at.is_(None)
            )
        ):
            await _close(session, member, now, "disbanded")
        _disband_row(clan, now)
        await session.commit()
        return clan


async def rename(session_factory, actor_id: int, new_name: str, *, now=None) -> Clan:
    now = now or utcnow()
    new_name = clean_name(new_name)
    async with session_factory() as session:
        clan = await _owned_clan(session, actor_id)
        if clan.renamed_at is not None and now - to_naive_utc(clan.renamed_at) < RENAME_COOLDOWN:
            raise ClanError(RENAME_TOO_SOON_ERROR)
        if name_key(new_name) != clan.name_key:
            await _ensure_name_free(session, new_name)
        clan.name, clan.name_key, clan.renamed_at = new_name, name_key(new_name), now
        await session.commit()
        return clan


async def set_icon(session_factory, actor_id: int, icon: str) -> None:
    if icon not in ICONS:
        raise ClanError(BAD_ICON_ERROR)
    async with session_factory() as session:
        clan = await _owned_clan(session, actor_id)
        clan.icon = icon
        await session.commit()


async def set_photo(session_factory, actor_id: int, file_id: str | None) -> None:
    async with session_factory() as session:
        clan = await _owned_clan(session, actor_id)
        clan.photo_file_id = file_id
        await session.commit()


async def set_motto(session_factory, actor_id: int, motto: str | None) -> None:
    cleaned = clean_motto(motto) if motto else None
    async with session_factory() as session:
        clan = await _owned_clan(session, actor_id)
        clan.motto = cleaned or None
        await session.commit()


async def regenerate_invite(session_factory, actor_id: int) -> str:
    """New invite code; every previously shared link stops working."""
    async with session_factory() as session:
        clan = await _led_clan(session, actor_id)
        clan.invite_code = await _unique_code(session)
        await session.commit()
        return clan.invite_code


async def link_home_chat(session_factory, actor_id: int, chat_id: int) -> Clan:
    """Make a group chat the clan's home (owner or officer). Current members
    are enrolled in that group so their trades broadcast there."""
    async with session_factory() as session:
        clan = await _led_clan(session, actor_id)
        other = await session.scalar(
            select(Clan).where(
                Clan.home_chat_id == chat_id, Clan.disbanded_at.is_(None), Clan.id != clan.id
            )
        )
        if other is not None:
            raise ClanError(HOME_TAKEN_ERROR)
        clan.home_chat_id = chat_id
        for uid in await session.scalars(
            select(ClanMembership.telegram_user_id).where(
                ClanMembership.clan_id == clan.id, ClanMembership.left_at.is_(None)
            )
        ):
            if await session.get(GroupMember, (chat_id, uid)) is None:
                session.add(GroupMember(chat_id=chat_id, telegram_user_id=uid))
        await session.commit()
        return clan


async def unlink_home_chat(session_factory, actor_id: int) -> None:
    async with session_factory() as session:
        clan = await _led_clan(session, actor_id)
        clan.home_chat_id = None
        await session.commit()


async def admin_reset(session_factory, clan_id: int) -> Clan | None:
    """Moderation (/admin): wipe a clan's photo and motto, and rename it to a
    neutral placeholder. The owner can rename it again immediately."""
    async with session_factory() as session:
        clan = await session.get(Clan, clan_id)
        if clan is None or clan.disbanded_at is not None:
            return None
        placeholder = f"Clan {clan.id}"
        clan.name, clan.name_key = placeholder, name_key(placeholder)
        clan.photo_file_id = clan.motto = clan.renamed_at = None
        await session.commit()
        return clan


# -- internals ----------------------------------------------------------------------


async def _open_stint(session, telegram_user_id: int) -> ClanMembership | None:
    return await session.scalar(
        select(ClanMembership)
        .join(Clan, Clan.id == ClanMembership.clan_id)
        .where(
            ClanMembership.telegram_user_id == telegram_user_id,
            ClanMembership.left_at.is_(None),
            Clan.disbanded_at.is_(None),
        )
    )


async def _open_count(session, clan_id: int) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(ClanMembership)
        .where(ClanMembership.clan_id == clan_id, ClanMembership.left_at.is_(None))
    )


async def _close(session, stint: ClanMembership, now: datetime, reason: str) -> None:
    stint.left_at, stint.left_reason = now, reason
    # Flush now: the partial unique index allows one OPEN stint per user, and
    # the caller may be about to insert the next one in the same transaction.
    await session.flush()


def _disband_row(clan: Clan, now: datetime) -> None:
    clan.disbanded_at = now
    clan.home_chat_id = None


async def _drop_home_broadcasts(session, clan: Clan, telegram_user_id: int) -> None:
    """Someone leaving the clan stops broadcasting into its home chat."""
    if clan is None or clan.home_chat_id is None:
        return
    row = await session.get(GroupMember, (clan.home_chat_id, telegram_user_id))
    if row is not None:
        await session.delete(row)


async def _ensure_name_free(session, name: str) -> None:
    taken = await session.scalar(
        select(Clan.id).where(Clan.name_key == name_key(name), Clan.disbanded_at.is_(None))
    )
    if taken is not None:
        raise ClanError(NAME_TAKEN_ERROR)


async def _unique_code(session) -> str:
    while True:
        code = new_invite_code()
        if await session.scalar(select(Clan.id).where(Clan.invite_code == code)) is None:
            return code


async def _kicked_recently(session, telegram_user_id: int, clan_id: int, now: datetime) -> bool:
    last_kick = await session.scalar(
        select(func.max(ClanMembership.left_at)).where(
            ClanMembership.telegram_user_id == telegram_user_id,
            ClanMembership.clan_id == clan_id,
            ClanMembership.left_reason == "kicked",
        )
    )
    return last_kick is not None and now - to_naive_utc(last_kick) < KICK_COOLDOWN


async def _switched_recently(session, telegram_user_id: int, now: datetime) -> bool:
    """Joined a clan less than 24h ago and that stint didn't end through no
    fault of their own (kicked or disbanded)? Then it's too soon to switch."""
    last = await session.scalar(
        select(ClanMembership)
        .where(ClanMembership.telegram_user_id == telegram_user_id)
        .order_by(ClanMembership.joined_at.desc(), ClanMembership.id.desc())
        .limit(1)
    )
    if last is None or last.left_reason in ("kicked", "disbanded"):
        return False
    return now - to_naive_utc(last.joined_at) < SWITCH_COOLDOWN


async def _actor_and_target(session, actor_id: int, target_id: int):
    actor = await _open_stint(session, actor_id)
    if actor is None:
        raise ClanError(NOT_IN_CLAN_ERROR)
    target = await _open_stint(session, target_id)
    if target is None or target.clan_id != actor.clan_id:
        raise ClanError(TARGET_NOT_MEMBER_ERROR)
    return actor, target


async def _owned_clan(session, actor_id: int) -> Clan:
    stint = await _open_stint(session, actor_id)
    if stint is None:
        raise ClanError(NOT_IN_CLAN_ERROR)
    if stint.role != OWNER:
        raise ClanError(NOT_OWNER_ERROR)
    return await session.get(Clan, stint.clan_id)


async def _led_clan(session, actor_id: int) -> Clan:
    stint = await _open_stint(session, actor_id)
    if stint is None:
        raise ClanError(NOT_IN_CLAN_ERROR)
    if stint.role not in (OWNER, OFFICER):
        raise ClanError(NOT_LEADER_ERROR)
    return await session.get(Clan, stint.clan_id)
