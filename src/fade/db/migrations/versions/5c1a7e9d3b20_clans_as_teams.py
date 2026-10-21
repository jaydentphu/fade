"""clans as teams (D-15): rename group tables, add clans + clan_memberships

Revision ID: 5c1a7e9d3b20
Revises: 0a1b2c3d4e5f
Create Date: 2026-10-21 12:00:00.000000

The old `clans`/`clan_members` tables described *group chats* registered via
/setup. They keep that job under honest names (`group_chats`/`group_members`),
and `clans` becomes a real team. The backfill turns every active registered
group into a clan whose home chat is that group, so nobody's clan vanishes.

Deliberately self-contained (no imports from fade.*): a migration must keep
working after the app code it was written beside has moved on.
"""

import re
import secrets
import string
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "5c1a7e9d3b20"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None

_NAME_OK = re.compile(r"[^A-Za-z0-9 .'!-]")
_CODE_ALPHABET = string.ascii_lowercase + string.digits
DEFAULT_ICON = "⚔️"


def _rename_pkey(old: str, new: str) -> None:
    # Postgres names a primary key's index "<table>_pkey" and does not rename it
    # with the table; a new `clans` table would then collide on "clans_pkey".
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"ALTER INDEX {old}_pkey RENAME TO {new}_pkey")


def upgrade() -> None:
    op.rename_table("clan_members", "group_members")
    _rename_pkey("clan_members", "group_members")
    op.rename_table("clans", "group_chats")
    _rename_pkey("clans", "group_chats")

    op.create_table(
        "clans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("name_key", sa.String(length=32), nullable=False),
        sa.Column("icon", sa.String(length=16), nullable=False),
        sa.Column("photo_file_id", sa.String(length=256), nullable=True),
        sa.Column("motto", sa.String(length=120), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("home_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("invite_code", sa.String(length=16), nullable=False),
        sa.Column("renamed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("disbanded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invite_code"),
    )
    op.create_index(
        "uq_clans_live_name",
        "clans",
        ["name_key"],
        unique=True,
        sqlite_where=sa.text("disbanded_at IS NULL"),
        postgresql_where=sa.text("disbanded_at IS NULL"),
    )
    op.create_index(
        "uq_clans_live_home_chat",
        "clans",
        ["home_chat_id"],
        unique=True,
        sqlite_where=sa.text("disbanded_at IS NULL AND home_chat_id IS NOT NULL"),
        postgresql_where=sa.text("disbanded_at IS NULL AND home_chat_id IS NOT NULL"),
    )
    op.create_table(
        "clan_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clan_id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_reason", sa.String(length=12), nullable=True),
        sa.ForeignKeyConstraint(["clan_id"], ["clans.id"]),
        sa.ForeignKeyConstraint(["telegram_user_id"], ["users.telegram_user_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clan_memberships_clan_id", "clan_memberships", ["clan_id"])
    op.create_index(
        "ix_clan_memberships_telegram_user_id", "clan_memberships", ["telegram_user_id"]
    )
    op.create_index(
        "uq_clan_memberships_open",
        "clan_memberships",
        ["telegram_user_id"],
        unique=True,
        sqlite_where=sa.text("left_at IS NULL"),
        postgresql_where=sa.text("left_at IS NULL"),
    )

    _backfill(op.get_bind())


def _clan_name(title: str, taken: set[str], fallback: str) -> tuple[str, str]:
    """Group title -> a valid, unique clan name (D-15 rules, inlined)."""
    base = " ".join(_NAME_OK.sub("", title or "").split())[:24].strip()
    if len(base) < 3:
        base = fallback
    name, n = base, 2
    while name.casefold() in taken:
        suffix = f" {n}"
        name = base[: 24 - len(suffix)].rstrip() + suffix
        n += 1
    taken.add(name.casefold())
    return name, name.casefold()


def _backfill(conn) -> None:
    groups = conn.execute(
        sa.text(
            "SELECT chat_id, title, created_by FROM group_chats "
            "WHERE active ORDER BY created_at, chat_id"
        )
    ).fetchall()
    if not groups:
        return
    users = {row[0] for row in conn.execute(sa.text("SELECT telegram_user_id FROM users"))}
    members = conn.execute(
        sa.text(
            "SELECT chat_id, telegram_user_id, joined_at FROM group_members "
            "ORDER BY joined_at, chat_id"
        )
    ).fetchall()
    joined_at = {(m.chat_id, m.telegram_user_id): m.joined_at for m in members}
    group_ids = {g.chat_id for g in groups}

    # One clan per user: whoever ran /setup owns their earliest group's clan;
    # everyone else lands in the earliest group they joined.
    assigned: dict[int, int] = {}
    owners: dict[int, int] = {}
    for g in groups:
        if g.created_by in users and g.created_by not in assigned:
            assigned[g.created_by] = g.chat_id
            owners[g.chat_id] = g.created_by
    for m in members:
        if m.chat_id in group_ids and m.telegram_user_id in users:
            assigned.setdefault(m.telegram_user_id, m.chat_id)

    taken: set[str] = set()
    now = datetime.now(UTC).replace(tzinfo=None)
    for index, g in enumerate(groups, start=1):
        roster = [uid for uid, chat in assigned.items() if chat == g.chat_id]
        roster.sort(key=lambda uid, chat=g.chat_id: str(joined_at.get((chat, uid)) or "~"))
        if not roster:
            continue
        owner = owners.get(g.chat_id, roster[0])
        name, key = _clan_name(g.title, taken, f"Clan {index}")
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
        conn.execute(
            sa.text(
                "INSERT INTO clans (name, name_key, icon, owner_id, home_chat_id, invite_code) "
                "VALUES (:name, :key, :icon, :owner, :chat, :code)"
            ),
            {
                "name": name,
                "key": key,
                "icon": DEFAULT_ICON,
                "owner": owner,
                "chat": g.chat_id,
                "code": code,
            },
        )
        clan_id = conn.execute(
            sa.text("SELECT id FROM clans WHERE invite_code = :code"), {"code": code}
        ).scalar_one()
        for uid in roster:
            # Keep the original join time: trades closed since then already
            # count for this clan, exactly as they counted for the group.
            since = joined_at.get((g.chat_id, uid)) or now
            conn.execute(
                sa.text(
                    "INSERT INTO clan_memberships (clan_id, telegram_user_id, role, joined_at) "
                    "VALUES (:clan, :uid, :role, :since)"
                ),
                {
                    "clan": clan_id,
                    "uid": uid,
                    "role": "owner" if uid == owner else "member",
                    "since": since,
                },
            )


def downgrade() -> None:
    op.drop_index("uq_clan_memberships_open", table_name="clan_memberships")
    op.drop_index("ix_clan_memberships_telegram_user_id", table_name="clan_memberships")
    op.drop_index("ix_clan_memberships_clan_id", table_name="clan_memberships")
    op.drop_table("clan_memberships")
    op.drop_index("uq_clans_live_home_chat", table_name="clans")
    op.drop_index("uq_clans_live_name", table_name="clans")
    op.drop_table("clans")
    op.rename_table("group_chats", "clans")
    _rename_pkey("group_chats", "clans")
    op.rename_table("group_members", "clan_members")
    _rename_pkey("group_members", "clan_members")
