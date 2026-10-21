"""The D-15 migration, run for real through Alembic on a seeded SQLite file:
group tables renamed, every active registered group backfilled into a clan,
one clan per user, and a clean downgrade."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "0a1b2c3d4e5f"
D15 = "5c1a7e9d3b20"


def alembic_config(db: Path, monkeypatch) -> Config:
    url = f"sqlite+aiosqlite:///{db.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)  # env.py prefers it over alembic.ini
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "src/fade/db/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def seed_old_schema(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        for uid in (1, 2, 3, 4):
            conn.execute(
                "INSERT INTO users (telegram_user_id, username, slippage_bps, hide_amounts) "
                "VALUES (?, ?, 200, 0)",
                (uid, f"u{uid}"),
            )
        conn.executemany(
            "INSERT INTO clans (chat_id, title, created_by, active, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (-10, "Degens [Anon] 🔥", 1, 1, "2026-07-01 00:00:00"),
                (-20, "degens anon", 2, 1, "2026-07-02 00:00:00"),  # name clash
                (-30, "Kicked Group", 3, 0, "2026-07-03 00:00:00"),  # inactive
            ],
        )
        conn.executemany(
            "INSERT INTO clan_members (chat_id, telegram_user_id, joined_at) VALUES (?, ?, ?)",
            [
                (-10, 1, "2026-07-01 00:00:00"),
                (-10, 4, "2026-07-01 06:00:00"),
                (-20, 2, "2026-07-02 00:00:00"),
                (-20, 4, "2026-07-02 06:00:00"),  # user 4's second group
                (-30, 3, "2026-07-03 00:00:00"),
            ],
        )


def test_migration_backfills_clans_and_downgrades(tmp_path, monkeypatch):
    db = tmp_path / "migrate.db"
    config = alembic_config(db, monkeypatch)
    command.upgrade(config, BEFORE)
    seed_old_schema(db)

    command.upgrade(config, D15)

    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"group_chats", "group_members", "clans", "clan_memberships"} <= tables
        assert "clan_members" not in tables
        # Group data is intact under the new names.
        assert conn.execute("SELECT COUNT(*) FROM group_chats").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM group_members").fetchone()[0] == 5

        clans = conn.execute(
            "SELECT id, name, name_key, owner_id, home_chat_id, invite_code FROM clans ORDER BY id"
        ).fetchall()
        # Inactive group -30 gets no clan; sanitized + de-duplicated names.
        assert [(c[1], c[3], c[4]) for c in clans] == [
            ("Degens Anon", 1, -10),
            ("degens anon 2", 2, -20),
        ]
        assert all(len(c[5]) == 10 and c[5].isalnum() for c in clans)

        stints = conn.execute(
            "SELECT clan_id, telegram_user_id, role, joined_at, left_at "
            "FROM clan_memberships ORDER BY telegram_user_id"
        ).fetchall()
        by_user = {s[1]: s for s in stints}
        assert set(by_user) == {1, 2, 4}  # user 3's only group was inactive
        assert by_user[1][2] == "owner" and by_user[2][2] == "owner"
        # One clan per user: user 4 lands in the EARLIEST group they joined,
        # keeping the original join time so past trades stay credited.
        assert by_user[4][0] == clans[0][0] and by_user[4][2] == "member"
        assert str(by_user[4][3]).startswith("2026-07-01 06:00:00")
        assert all(s[4] is None for s in stints)

    command.downgrade(config, BEFORE)
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"clans", "clan_members"} <= tables
        assert not {"group_chats", "group_members", "clan_memberships"} & tables
        assert conn.execute("SELECT COUNT(*) FROM clans").fetchone()[0] == 3
        assert conn.execute("SELECT title FROM clans WHERE chat_id = -10").fetchone()[0] == (
            "Degens [Anon] 🔥"
        )


def test_migration_head_matches_the_models(tmp_path, monkeypatch):
    """No drift between the migration and SQLAlchemy models (autogenerate
    finds nothing to add or drop for the D-15 tables)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from fade.db.models import Base

    db = tmp_path / "head.db"
    command.upgrade(alembic_config(db, monkeypatch), "head")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    d15 = {"clans", "clan_memberships", "group_chats", "group_members"}
    relevant = [
        d for d in diff
        if any(t in str(d) for t in d15) and d[0] in ("add_table", "remove_table", "add_column",
                                                      "remove_column", "add_index", "remove_index")
    ]  # fmt: skip
    assert relevant == []
