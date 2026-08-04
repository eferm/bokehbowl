"""The verified-only users migration, exercised on a fixture database at the
base revision."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text


REPO_ROOT = Path(__file__).resolve().parent.parent

BASE_REVISION = "9157d1772fb8"

DAY_1 = "2026-01-01 09:00:00.000000"
DAY_2 = "2026-01-02 09:00:00.000000"
DAY_3 = "2026-01-03 09:00:00.000000"
DAY_4 = "2026-01-04 09:00:00.000000"

ANALYTICAL_WAY = {
    "addressee": "Ada Lovelace",
    "address_line1": "12 Analytical Way",
    "address_line2": None,
    "city": "London",
    "region": None,
    "postal_code": "N1 9GU",
    "country": "United Kingdom",
}
OCKHAM_PARK = {
    "addressee": "Ada Lovelace",
    "address_line1": "1 Ockham Park",
    "address_line2": None,
    "city": "Surrey",
    "region": None,
    "postal_code": "GU23 6NQ",
    "country": "United Kingdom",
}
MARK_II_LANE = {
    "addressee": "Grace Hopper",
    "address_line1": "3 Mark II Lane",
    "address_line2": "Apt 2",
    "city": "Arlington",
    "region": "VA",
    "postal_code": "22201",
    "country": "United States",
}
DORSET_STREET = {
    "addressee": "Charles Babbage",
    "address_line1": "1 Dorset Street",
    "address_line2": None,
    "city": "London",
    "region": None,
    "postal_code": "W1U 4EG",
    "country": "United Kingdom",
}

USERS = [
    {
        "id": "ada",
        "email": "ada@example.com",
        "created_at": DAY_1,
        "verified_at": DAY_3,
        "unsubscribed_at": None,
    },
    {
        "id": "grace",
        "email": "grace@example.com",
        "created_at": DAY_1,
        "verified_at": DAY_1,
        "unsubscribed_at": DAY_4,
    },
    {
        "id": "charles",
        "email": "charles@example.com",
        "created_at": DAY_2,
        "verified_at": None,
        "unsubscribed_at": None,
    },
]

ADDRESSES = [
    {
        "id": "v-ada-1",
        "user_id": "ada",
        **ANALYTICAL_WAY,
        "derived_from_id": None,
        "created_at": DAY_1,
    },
    {
        "id": "v-ada-3",
        "user_id": "ada",
        **OCKHAM_PARK,
        "derived_from_id": None,
        "created_at": DAY_3,
    },
    {
        "id": "v-grace-1",
        "user_id": "grace",
        **MARK_II_LANE,
        "derived_from_id": None,
        "created_at": DAY_1,
    },
    {
        "id": "v-charles-1",
        "user_id": "charles",
        **DORSET_STREET,
        "derived_from_id": None,
        "created_at": DAY_2,
    },
]

EDITIONS = [{"id": "m-1", "title": "First light", "created_at": DAY_2}]

USER_SESSIONS = [{"token": "tok-ada", "user_id": "ada", "created_at": DAY_3}]

# mp-ada prints an earlier address than the one ada now has on file, so the
# upgrade files a print version pinned to that earlier row.
MAILPIECES = [
    {
        "id": "mp-ada",
        "edition_id": "m-1",
        "user_id": "ada",
        "address_id": "v-ada-1",
        "sent_at": DAY_3,
    },
    {
        "id": "mp-grace",
        "edition_id": "m-1",
        "user_id": "grace",
        "address_id": "v-grace-1",
        "sent_at": DAY_3,
    },
]

DERIVED_ROW = {
    "id": "d-ada-1",
    "user_id": "ada",
    "addressee": "ADA LOVELACE",
    "address_line1": "1 OCKHAM PK",
    "address_line2": None,
    "city": "SURREY",
    "region": None,
    "postal_code": "GU23 6NQ",
    "country": "UNITED KINGDOM",
    "derived_from_id": "v-ada-3",
    "created_at": DAY_4,
}


def insert(conn: Connection, table: str, entries: list[dict]) -> None:
    columns = ", ".join(entries[0])
    placeholders = ", ".join(f":{column}" for column in entries[0])
    conn.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), entries
    )


def rows(engine: Engine, sql: str) -> list[dict]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(sql))]


def by_id(engine: Engine, sql: str, key: str = "id") -> dict[str, dict]:
    return {row[key]: row for row in rows(engine, sql)}


@pytest.fixture
def base_database(tmp_path, monkeypatch) -> tuple[Config, Engine]:
    """A database at the base revision, loaded with the fixture rows."""
    url = f"sqlite:///{tmp_path}/bokehbowl.db"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("OPERATOR_TZ", "America/New_York")
    config = Config()
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(config, BASE_REVISION)
    engine = create_engine(url)
    with engine.begin() as conn:
        insert(conn, "users", USERS)
        insert(conn, "addresses", ADDRESSES)
        insert(conn, "editions", EDITIONS)
        insert(conn, "user_sessions", USER_SESSIONS)
        insert(conn, "mailpieces", MAILPIECES)
    return config, engine


def test_base_revision_creates_the_schema(base_database):
    """A fresh database reaches head from the base revision alone."""
    config, engine = base_database
    command.upgrade(config, "head")

    tables = {
        row["name"]
        for row in rows(engine, "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "users",
        "addresses",
        "normalized_addresses",
        "editions",
        "mailpieces",
        "user_sessions",
        "login_codes",
        "admin_sessions",
    } <= tables
    login_code_columns = {
        row["name"] for row in rows(engine, "PRAGMA table_info(login_codes)")
    }
    assert "attempts" not in login_code_columns


def test_head_keeps_verified_users_and_drops_unverified(base_database):
    config, engine = base_database
    command.upgrade(config, "head")

    users = by_id(engine, "SELECT * FROM users")
    assert set(users) == {"ada", "grace"}
    assert users["ada"] == {
        "id": "ada",
        "email": "ada@example.com",
        "unsubscribed_at": None,
        "created_at": DAY_3,
    }
    assert users["grace"]["unsubscribed_at"] == DAY_4

    addresses = by_id(engine, "SELECT * FROM addresses")
    assert set(addresses) == {"v-ada-1", "v-ada-3", "v-grace-1"}
    assert "derived_from_id" not in addresses["v-ada-1"]

    assert rows(engine, "SELECT * FROM user_sessions") == [
        {"token": "tok-ada", "user_id": "ada", "created_at": DAY_3}
    ]
    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert set(mailpieces) == {"mp-ada", "mp-grace"}
    assert "address_id" not in mailpieces["mp-ada"]
    assert mailpieces["mp-ada"]["sent_on"] == "2026-01-03"

    normalized = by_id(engine, "SELECT * FROM normalized_addresses")
    ada_print = normalized[mailpieces["mp-ada"]["normalized_address_id"]]
    assert ada_print["address_id"] == "v-ada-1"
    assert ada_print["user_id"] == "ada"
    assert ada_print["addressee"] == "Ada Lovelace"
    assert ada_print["address_line1"] == "12 Analytical Way"
    assert ada_print["created_at"] == DAY_3
    grace_print = normalized[mailpieces["mp-grace"]["normalized_address_id"]]
    assert grace_print["address_id"] == "v-grace-1"
    assert grace_print["user_id"] == "grace"
    assert grace_print["address_line1"] == "3 Mark II Lane"
    assert grace_print["created_at"] == DAY_3


def test_head_stops_on_a_mailpiece_held_by_an_unverified_user(base_database):
    """Charles never verified, so his rows stay behind and his mailpiece has
    nowhere to land. The upgrade names it and leaves the database as it was."""
    config, engine = base_database
    with engine.begin() as conn:
        insert(
            conn,
            "mailpieces",
            [
                {
                    "id": "mp-charles",
                    "edition_id": "m-1",
                    "user_id": "charles",
                    "address_id": "v-charles-1",
                    "sent_at": DAY_4,
                }
            ],
        )

    with pytest.raises(RuntimeError, match="mp-charles"):
        command.upgrade(config, "head")

    assert by_id(engine, "SELECT * FROM users")["charles"]["verified_at"] is None
    assert set(by_id(engine, "SELECT * FROM mailpieces")) == {
        "mp-ada",
        "mp-grace",
        "mp-charles",
    }


def test_head_stops_on_a_mailpiece_carrying_another_users_address(base_database):
    config, engine = base_database
    command.upgrade(config, "4fd713a86b9c")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM mailpieces WHERE id = 'mp-grace'"))
        conn.execute(
            text("UPDATE mailpieces SET user_id = 'grace' WHERE id = 'mp-ada'")
        )

    with pytest.raises(RuntimeError, match="mp-ada"):
        command.upgrade(config, "head")


def test_head_satisfies_every_foreign_key(base_database):
    config, engine = base_database
    command.upgrade(config, "head")

    assert rows(engine, "PRAGMA foreign_key_check") == []


def test_head_backfills_the_operator_local_sent_date(base_database):
    config, engine = base_database
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mailpieces SET sent_at = '2026-01-03 02:00:00.000000'"
                " WHERE id = 'mp-ada'"
            )
        )

    command.upgrade(config, "head")

    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"]["sent_on"] == "2026-01-02"
    assert mailpieces["mp-grace"]["sent_on"] == "2026-01-03"


def test_head_defaults_the_backfill_timezone_to_utc(base_database, monkeypatch):
    config, engine = base_database
    monkeypatch.delenv("OPERATOR_TZ")
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE mailpieces SET sent_at = '2026-01-03 02:00:00.000000'"
                " WHERE id = 'mp-ada'"
            )
        )

    command.upgrade(config, "head")

    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"]["sent_on"] == "2026-01-03"


def test_head_moves_derived_rows_into_normalized_addresses(base_database):
    config, engine = base_database
    with engine.begin() as conn:
        insert(conn, "addresses", [DERIVED_ROW])
        conn.execute(
            text("UPDATE mailpieces SET address_id = 'd-ada-1' WHERE id = 'mp-ada'")
        )
    command.upgrade(config, "head")

    normalized = by_id(engine, "SELECT * FROM normalized_addresses")
    assert normalized["d-ada-1"] == {
        "id": "d-ada-1",
        "address_id": "v-ada-3",
        "user_id": "ada",
        "addressee": "ADA LOVELACE",
        "address_line1": "1 OCKHAM PK",
        "address_line2": None,
        "city": "SURREY",
        "region": None,
        "postal_code": "GU23 6NQ",
        "country": "UNITED KINGDOM",
        "created_at": DAY_4,
    }
    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"]["normalized_address_id"] == "d-ada-1"

    command.downgrade(config, BASE_REVISION)
    login_code_columns = {
        row["name"] for row in rows(engine, "PRAGMA table_info(login_codes)")
    }
    assert "attempts" in login_code_columns
    addresses = by_id(engine, "SELECT * FROM addresses")
    assert addresses["d-ada-1"] == DERIVED_ROW
    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"]["address_id"] == "d-ada-1"
    # d-ada-1 edits its address, so it comes back as a derived row of its own.
    # Grace's print version copies hers verbatim and collapses into it.
    assert mailpieces["mp-grace"]["address_id"] == "v-grace-1"
    assert addresses["v-grace-1"]["derived_from_id"] is None
    assert addresses["v-grace-1"]["address_line1"] == "3 Mark II Lane"


def test_round_trip_from_head_to_the_base_revision_and_back(base_database):
    """Verified users and their related rows survive a full round trip."""
    config, engine = base_database
    command.upgrade(config, "head")
    command.downgrade(config, BASE_REVISION)

    users = by_id(engine, "SELECT * FROM users", key="email")
    assert set(users) == {"ada@example.com", "grace@example.com"}
    assert users["ada@example.com"]["verified_at"] == DAY_3
    assert users["grace@example.com"]["unsubscribed_at"] == DAY_4

    addresses = by_id(engine, "SELECT * FROM addresses")
    assert set(addresses) == {"v-ada-1", "v-ada-3", "v-grace-1"}
    assert addresses["v-ada-1"] == ADDRESSES[0]
    assert addresses["v-grace-1"] == ADDRESSES[2]

    assert rows(engine, "SELECT * FROM editions") == EDITIONS
    assert rows(engine, "SELECT * FROM user_sessions") == USER_SESSIONS
    assert by_id(engine, "SELECT * FROM mailpieces")["mp-grace"] == MAILPIECES[1]

    command.upgrade(config, "head")
    assert rows(engine, "PRAGMA foreign_key_check") == []
    assert set(by_id(engine, "SELECT * FROM users", key="email")) == {
        "ada@example.com",
        "grace@example.com",
    }
    assert len(rows(engine, "SELECT * FROM mailpieces")) == 2
