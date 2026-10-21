"""Clan rules (D-15): every permission edge, cap, and cooldown lives in
fade.social.clans and is pinned here."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from fade.db.models import ClanMembership, GroupChat, GroupMember, User
from fade.social import clans
from fade.social.clans import ClanError

OWNER, OFFICER, ALICE, BOB, CAROL = 11, 12, 13, 14, 15
T0 = datetime(2026, 10, 1, 12, 0)  # naive UTC, like the DB


async def users(session_factory, *ids):
    async with session_factory() as session:
        for uid in ids:
            session.add(User(telegram_user_id=uid, username=f"u{uid}", attestation_accepted_at=T0))
        await session.commit()


async def clan_with(session_factory, *joiners, name="Shark Tank", at=T0):
    """OWNER's clan with `joiners` joined 2 days later (past switch cooldowns)."""
    clan = await clans.create(session_factory, OWNER, name, "🦈", now=at)
    for uid in joiners:
        await clans.join(session_factory, uid, clan.invite_code, now=at + timedelta(days=2))
    return clan


@pytest.fixture
async def crew(session_factory):
    await users(session_factory, OWNER, OFFICER, ALICE, BOB, CAROL)
    clan = await clan_with(session_factory, OFFICER, ALICE, BOB)
    await clans.set_role(session_factory, OWNER, OFFICER, clans.OFFICER)
    return clan


LATER = T0 + timedelta(days=5)


# -- names -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Shark   Tank ", "Shark Tank"),
        ("Degens Anon!", "Degens Anon!"),
        ("O'Brien-9", "O'Brien-9"),
    ],
)
def test_valid_names_are_normalized(raw, expected):
    assert clans.clean_name(raw) == expected


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("ab", clans.NAME_LENGTH_ERROR),
        ("x" * 25, clans.NAME_LENGTH_ERROR),
        ("under_score", clans.NAME_CHARS_ERROR),  # sanitize() would strip it
        ("[admin]", clans.NAME_CHARS_ERROR),
        ("🦈 Sharks", clans.NAME_CHARS_ERROR),  # cards render Latin only
        ("...", clans.NAME_CHARS_ERROR),  # needs at least one letter or digit
    ],
)
def test_invalid_names_are_rejected(raw, error):
    with pytest.raises(ClanError, match=error.split("(")[0][:20]):
        clans.clean_name(raw)


async def test_names_are_unique_case_insensitively(session_factory, crew):
    await users(session_factory, 99)
    with pytest.raises(ClanError, match="taken"):
        await clans.create(session_factory, 99, "shark  TANK")


async def test_a_disbanded_clans_name_is_free_again(session_factory, crew):
    for uid in (OFFICER, ALICE, BOB):
        await clans.kick(session_factory, OWNER, uid, now=LATER)
    await clans.disband(session_factory, OWNER, now=LATER)
    await users(session_factory, 99)
    again = await clans.create(session_factory, 99, "Shark Tank")
    assert again.name == "Shark Tank"


# -- create / join ------------------------------------------------------------------


async def test_create_makes_the_creator_owner(session_factory):
    await users(session_factory, OWNER)
    clan = await clans.create(session_factory, OWNER, "Wolfpack", "🐺", now=T0)
    mine = await clans.current(session_factory, OWNER)
    assert mine[0].id == clan.id and mine[1].role == clans.OWNER
    assert clan.icon == "🐺" and len(clan.invite_code) == clans.INVITE_CODE_LEN
    assert clan.invite_code.isalnum() and clan.invite_code == clan.invite_code.lower()


async def test_icon_must_come_from_the_picker(session_factory):
    await users(session_factory, OWNER)
    with pytest.raises(ClanError):
        await clans.create(session_factory, OWNER, "Wolfpack", "<script>")


async def test_one_clan_per_user(session_factory, crew):
    with pytest.raises(ClanError, match="already in a clan"):
        await clans.create(session_factory, ALICE, "Side Hustle")


async def test_join_needs_the_current_invite_code(session_factory, crew):
    old = crew.invite_code
    await clans.regenerate_invite(session_factory, OFFICER)  # officers can
    with pytest.raises(ClanError, match="no longer valid"):
        await clans.join(session_factory, CAROL, old, now=LATER)


async def test_members_cannot_regenerate_the_invite(session_factory, crew):
    with pytest.raises(ClanError, match="owner or an officer"):
        await clans.regenerate_invite(session_factory, ALICE)


async def test_clan_cap_is_enforced(session_factory, crew, monkeypatch):
    monkeypatch.setattr(clans, "MEMBER_CAP", 4)
    with pytest.raises(ClanError, match="full"):
        await clans.join(session_factory, CAROL, crew.invite_code, now=LATER)


async def test_joining_another_clan_leaves_the_old_one(session_factory, crew):
    await users(session_factory, 99)
    other = await clans.create(session_factory, 99, "Wolfpack", now=T0)
    await clans.join(session_factory, ALICE, other.invite_code, now=LATER)

    assert (await clans.current(session_factory, ALICE))[0].id == other.id
    async with session_factory() as session:
        stints = (
            await session.scalars(
                select(ClanMembership).where(ClanMembership.telegram_user_id == ALICE)
            )
        ).all()
    assert [(s.clan_id, s.left_reason) for s in stints] == [(crew.id, "left"), (other.id, None)]


async def test_switching_is_limited_to_once_a_day(session_factory, crew):
    await users(session_factory, 99)
    other = await clans.create(session_factory, 99, "Wolfpack", now=T0)
    joined_alice = T0 + timedelta(days=2)  # from the crew fixture
    with pytest.raises(ClanError, match="once every 24 hours"):
        await clans.join(
            session_factory, ALICE, other.invite_code, now=joined_alice + timedelta(hours=23)
        )
    await clans.join(
        session_factory, ALICE, other.invite_code, now=joined_alice + timedelta(hours=25)
    )


async def test_owner_cannot_walk_out_on_their_clan(session_factory, crew):
    await users(session_factory, 99)
    other = await clans.create(session_factory, 99, "Wolfpack", now=T0)
    with pytest.raises(ClanError, match="Make someone else owner"):
        await clans.join(session_factory, OWNER, other.invite_code, now=LATER)
    with pytest.raises(ClanError, match="Make someone else owner"):
        await clans.leave(session_factory, OWNER, now=LATER)


async def test_last_member_leaving_disbands(session_factory):
    await users(session_factory, OWNER)
    await clans.create(session_factory, OWNER, "Lone Wolf", now=T0)
    clan = await clans.leave(session_factory, OWNER, now=LATER)
    assert clan.disbanded_at is not None
    assert await clans.current(session_factory, OWNER) is None


# -- kick / roles / transfer ---------------------------------------------------------


async def test_owner_kicks_anyone_and_they_cannot_rejoin_for_a_day(session_factory, crew):
    gone = await clans.kick(session_factory, OWNER, OFFICER, now=LATER)
    assert gone.telegram_user_id == OFFICER
    assert await clans.current(session_factory, OFFICER) is None
    with pytest.raises(ClanError, match="removed from this clan recently"):
        await clans.join(session_factory, OFFICER, crew.invite_code, now=LATER + timedelta(hours=1))
    await clans.join(session_factory, OFFICER, crew.invite_code, now=LATER + timedelta(hours=25))


async def test_kicked_user_can_join_another_clan_right_away(session_factory, crew):
    """The switch cooldown punishes hopping, not being kicked."""
    await users(session_factory, 99)
    other = await clans.create(session_factory, 99, "Wolfpack", now=T0)
    await clans.kick(session_factory, OWNER, ALICE, now=T0 + timedelta(days=2, hours=1))
    await clans.join(session_factory, ALICE, other.invite_code, now=T0 + timedelta(days=2, hours=2))


async def test_officers_kick_members_but_not_officers_or_the_owner(session_factory, crew):
    await clans.kick(session_factory, OFFICER, ALICE, now=LATER)
    await clans.set_role(session_factory, OWNER, BOB, clans.OFFICER)
    with pytest.raises(ClanError, match="Officers can only remove members"):
        await clans.kick(session_factory, OFFICER, BOB, now=LATER)
    with pytest.raises(ClanError, match="Officers can only remove members"):
        await clans.kick(session_factory, OFFICER, OWNER, now=LATER)


async def test_members_cannot_kick(session_factory, crew):
    with pytest.raises(ClanError, match="owner or an officer"):
        await clans.kick(session_factory, ALICE, BOB, now=LATER)


async def test_cannot_kick_outsiders_or_yourself(session_factory, crew):
    with pytest.raises(ClanError, match="isn't in your clan"):
        await clans.kick(session_factory, OWNER, CAROL, now=LATER)
    with pytest.raises(ClanError, match="yourself"):
        await clans.kick(session_factory, OWNER, OWNER, now=LATER)


async def test_only_the_owner_promotes(session_factory, crew):
    with pytest.raises(ClanError, match="Only the clan owner"):
        await clans.set_role(session_factory, OFFICER, ALICE, clans.OFFICER)
    await clans.set_role(session_factory, OWNER, OFFICER, clans.MEMBER)
    assert (await clans.current(session_factory, OFFICER))[1].role == clans.MEMBER


async def test_transfer_swaps_owner_and_officer(session_factory, crew):
    await clans.transfer(session_factory, OWNER, ALICE)
    assert (await clans.current(session_factory, ALICE))[1].role == clans.OWNER
    assert (await clans.current(session_factory, OWNER))[1].role == clans.OFFICER
    assert (await clans.get_clan(session_factory, crew.id)).owner_id == ALICE
    await clans.leave(session_factory, OWNER, now=LATER)  # no longer stuck


# -- identity ------------------------------------------------------------------------


async def test_rename_is_owner_only_and_weekly(session_factory, crew):
    with pytest.raises(ClanError, match="Only the clan owner"):
        await clans.rename(session_factory, OFFICER, "Mutiny", now=LATER)
    await clans.rename(session_factory, OWNER, "Great Whites", now=LATER)
    with pytest.raises(ClanError, match="once every 7 days"):
        await clans.rename(session_factory, OWNER, "Hammerheads", now=LATER + timedelta(days=6))
    renamed = await clans.rename(
        session_factory, OWNER, "Hammerheads", now=LATER + timedelta(days=7)
    )
    assert renamed.name == "Hammerheads"


async def test_photo_and_motto(session_factory, crew):
    await clans.set_photo(session_factory, OWNER, "AgACAgFakeFileId")
    await clans.set_motto(session_factory, OWNER, "  Fade   the   public  ")
    clan = await clans.get_clan(session_factory, crew.id)
    assert clan.photo_file_id == "AgACAgFakeFileId"
    assert clan.motto == "Fade the public"
    with pytest.raises(ClanError, match="80 characters"):
        await clans.set_motto(session_factory, OWNER, "x" * 81)
    with pytest.raises(ClanError, match="Only the clan owner"):
        await clans.set_photo(session_factory, ALICE, "AgACAgOther")


async def test_admin_reset_wipes_identity(session_factory, crew):
    await clans.set_photo(session_factory, OWNER, "AgACAgFakeFileId")
    await clans.set_motto(session_factory, OWNER, "something rude")
    clan = await clans.admin_reset(session_factory, crew.id)
    assert clan.name == f"Clan {crew.id}"
    assert clan.photo_file_id is None and clan.motto is None


# -- home chat -----------------------------------------------------------------------


async def test_home_chat_enrolls_members_for_broadcasts(session_factory, crew):
    chat = -500
    async with session_factory() as session:
        session.add(GroupChat(chat_id=chat, title="Shark Chat", created_by=OWNER))
        await session.commit()
    await clans.link_home_chat(session_factory, OFFICER, chat)
    await clans.join(session_factory, CAROL, crew.invite_code, now=LATER)
    await clans.kick(session_factory, OWNER, ALICE, now=LATER)

    async with session_factory() as session:
        enrolled = set(
            await session.scalars(
                select(GroupMember.telegram_user_id).where(GroupMember.chat_id == chat)
            )
        )
    assert enrolled == {OWNER, OFFICER, BOB, CAROL}  # Alice was kicked out of it


async def test_a_chat_is_home_to_one_clan_only(session_factory, crew):
    await users(session_factory, 99)
    await clans.create(session_factory, 99, "Wolfpack", now=T0)
    await clans.link_home_chat(session_factory, OWNER, -500)
    with pytest.raises(ClanError, match="already home"):
        await clans.link_home_chat(session_factory, 99, -500)
    with pytest.raises(ClanError, match="owner or an officer"):
        await clans.link_home_chat(session_factory, ALICE, -600)


async def test_members_listing_order(session_factory, crew):
    roster = await clans.members(session_factory, crew.id)
    assert [m.telegram_user_id for m in roster] == [OWNER, OFFICER, ALICE, BOB]
    assert roster[0].role == clans.OWNER and roster[1].role == clans.OFFICER


def test_invite_link_format():
    assert clans.invite_link("fadepvp_bot", "abc123def0") == (
        "https://t.me/fadepvp_bot?start=clanabc123def0"
    )


def test_now_is_naive_utc():
    now = clans.utcnow()
    assert now.tzinfo is None
    assert abs(now - datetime.now(UTC).replace(tzinfo=None)) < timedelta(seconds=5)
