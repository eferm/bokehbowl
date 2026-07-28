"""The data-copying migrations from the recipients schema through the
verified-only users schema, exercised on a fixture database."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, text


REPO_ROOT = Path(__file__).resolve().parent.parent

OLD_REVISION = "5ea8adf60cba"
MID_REVISION = "9157d1772fb8"

DAY_1 = "2026-01-01 09:00:00.000000"
DAY_2 = "2026-01-02 09:00:00.000000"
DAY_3 = "2026-01-03 09:00:00.000000"
DAY_4 = "2026-01-04 09:00:00.000000"

ANALYTICAL_WAY = {
    "name": "Ada Lovelace",
    "address_line1": "12 Analytical Way",
    "address_line2": None,
    "city": "London",
    "region": None,
    "postal_code": "N1 9GU",
    "country": "United Kingdom",
}
OCKHAM_PARK = {
    "name": "Ada Lovelace",
    "address_line1": "1 Ockham Park",
    "address_line2": None,
    "city": "Surrey",
    "region": None,
    "postal_code": "GU23 6NQ",
    "country": "United Kingdom",
}
MARK_II_LANE = {
    "name": "Grace Hopper",
    "address_line1": "3 Mark II Lane",
    "address_line2": "Apt 2",
    "city": "Arlington",
    "region": "VA",
    "postal_code": "22201",
    "country": "United States",
}
DORSET_STREET = {
    "name": "Charles Babbage",
    "address_line1": "1 Dorset Street",
    "address_line2": None,
    "city": "London",
    "region": None,
    "postal_code": "W1U 4EG",
    "country": "United Kingdom",
}

RECIPIENTS = [
    {
        "id": "ada",
        "email": "ada@example.com",
        **OCKHAM_PARK,
        "created_at": DAY_1,
        "verified_at": DAY_1,
        "unsubscribed_at": None,
    },
    {
        "id": "grace",
        "email": "grace@example.com",
        **MARK_II_LANE,
        "created_at": DAY_1,
        "verified_at": DAY_1,
        "unsubscribed_at": DAY_4,
    },
    {
        "id": "charles",
        "email": "charles@example.com",
        **DORSET_STREET,
        "created_at": DAY_2,
        "verified_at": None,
        "unsubscribed_at": None,
    },
]

# v-ada-2 re-saves v-ada-1's fields verbatim: the upgrade collapses the pair
# into one address row and repoints anything that referenced the re-save.
RECIPIENT_VERSIONS = [
    {
        "id": "v-ada-1",
        "recipient_id": "ada",
        "email": "ada@example.com",
        **ANALYTICAL_WAY,
        "valid_from": DAY_1,
    },
    {
        "id": "v-ada-2",
        "recipient_id": "ada",
        "email": "ada@example.com",
        **ANALYTICAL_WAY,
        "valid_from": DAY_2,
    },
    {
        "id": "v-ada-3",
        "recipient_id": "ada",
        "email": "ada@example.com",
        **OCKHAM_PARK,
        "valid_from": DAY_3,
    },
    {
        "id": "v-grace-1",
        "recipient_id": "grace",
        "email": "grace@example.com",
        **MARK_II_LANE,
        "valid_from": DAY_1,
    },
    {
        "id": "v-charles-1",
        "recipient_id": "charles",
        "email": "charles@example.com",
        **DORSET_STREET,
        "valid_from": DAY_2,
    },
]

MAILINGS = [{"id": "m-1", "title": "First light", "created_at": DAY_2}]

RECIPIENT_SESSIONS = [{"token": "tok-ada", "recipient_id": "ada", "created_at": DAY_3}]

MAILPIECES = [
    {
        "id": "mp-ada",
        "mailing_id": "m-1",
        "recipient_id": "ada",
        "recipient_version_id": "v-ada-2",
        "sent_at": DAY_3,
    },
    {
        "id": "mp-grace",
        "mailing_id": "m-1",
        "recipient_id": "grace",
        "recipient_version_id": "v-grace-1",
        "sent_at": DAY_3,
    },
]


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
def old_database(tmp_path, monkeypatch) -> tuple[Config, Engine]:
    """A database at the recipients-era revision, loaded with the fixture rows."""
    url = f"sqlite:///{tmp_path}/bokehbowl.db"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config()
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(config, OLD_REVISION)
    engine = create_engine(url)
    with engine.begin() as conn:
        insert(conn, "recipients", RECIPIENTS)
        insert(conn, "recipient_versions", RECIPIENT_VERSIONS)
        insert(conn, "mailings", MAILINGS)
        insert(conn, "recipient_sessions", RECIPIENT_SESSIONS)
        insert(conn, "mailpieces", MAILPIECES)
    return config, engine


def test_upgrade_carries_users_and_editions(old_database):
    config, engine = old_database
    command.upgrade(config, MID_REVISION)

    users = by_id(engine, "SELECT * FROM users")
    assert set(users) == {"ada", "grace", "charles"}
    assert users["ada"] == {
        "id": "ada",
        "email": "ada@example.com",
        "verified_at": DAY_1,
        "unsubscribed_at": None,
        "created_at": DAY_1,
    }
    assert users["grace"]["unsubscribed_at"] == DAY_4
    assert users["charles"]["verified_at"] is None

    assert rows(engine, "SELECT * FROM editions") == [
        {"id": "m-1", "title": "First light", "created_at": DAY_2}
    ]


def test_upgrade_dedupes_addresses_and_keeps_version_ids(old_database):
    config, engine = old_database
    command.upgrade(config, MID_REVISION)

    addresses = by_id(engine, "SELECT * FROM addresses")
    assert set(addresses) == {"v-ada-1", "v-ada-3", "v-grace-1", "v-charles-1"}
    assert addresses["v-ada-1"]["address_line1"] == "12 Analytical Way"
    assert addresses["v-ada-1"]["created_at"] == DAY_1
    assert addresses["v-ada-3"]["address_line1"] == "1 Ockham Park"
    assert addresses["v-ada-3"]["created_at"] == DAY_3
    assert all(a["derived_from_id"] is None for a in addresses.values())
    assert addresses["v-ada-1"]["addressee"] == "Ada Lovelace"


def test_upgrade_repoints_mailpieces_at_deduped_addresses(old_database):
    config, engine = old_database
    command.upgrade(config, MID_REVISION)

    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"] == {
        "id": "mp-ada",
        "edition_id": "m-1",
        "user_id": "ada",
        "address_id": "v-ada-1",
        "sent_at": DAY_3,
    }
    assert mailpieces["mp-grace"]["address_id"] == "v-grace-1"


def test_upgrade_preserves_sessions(old_database):
    config, engine = old_database
    command.upgrade(config, MID_REVISION)

    assert rows(engine, "SELECT * FROM user_sessions") == [
        {"token": "tok-ada", "user_id": "ada", "created_at": DAY_3}
    ]


def test_upgrade_satisfies_every_foreign_key(old_database):
    config, engine = old_database
    command.upgrade(config, MID_REVISION)

    assert rows(engine, "PRAGMA foreign_key_check") == []


def test_downgrade_restores_the_recipient_schema(old_database):
    config, engine = old_database
    command.upgrade(config, MID_REVISION)
    command.downgrade(config, OLD_REVISION)

    recipients = by_id(engine, "SELECT * FROM recipients")
    assert recipients == {r["id"]: r for r in RECIPIENTS}

    versions = by_id(engine, "SELECT * FROM recipient_versions")
    assert set(versions) == {"v-ada-1", "v-ada-3", "v-grace-1", "v-charles-1"}
    assert versions["v-ada-1"] == RECIPIENT_VERSIONS[0]

    assert rows(engine, "SELECT * FROM mailings") == MAILINGS
    assert rows(engine, "SELECT * FROM recipient_sessions") == RECIPIENT_SESSIONS

    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"]["recipient_version_id"] == "v-ada-1"
    assert mailpieces["mp-grace"] == MAILPIECES[1]


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


def test_head_keeps_verified_users_and_drops_unverified(old_database):
    config, engine = old_database
    command.upgrade(config, "head")

    users = by_id(engine, "SELECT * FROM users")
    assert set(users) == {"ada", "grace"}
    assert users["ada"] == {
        "id": "ada",
        "email": "ada@example.com",
        "unsubscribed_at": None,
        "created_at": DAY_1,
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

    normalized = by_id(engine, "SELECT * FROM normalized_addresses")
    ada_print = normalized[mailpieces["mp-ada"]["normalized_address_id"]]
    assert ada_print["address_id"] == "v-ada-1"
    assert ada_print["addressee"] == "Ada Lovelace"
    assert ada_print["address_line1"] == "12 Analytical Way"
    assert ada_print["created_at"] == DAY_3
    grace_print = normalized[mailpieces["mp-grace"]["normalized_address_id"]]
    assert grace_print["address_id"] == "v-grace-1"
    assert grace_print["address_line1"] == "3 Mark II Lane"
    assert grace_print["created_at"] == DAY_3


def test_head_satisfies_every_foreign_key(old_database):
    config, engine = old_database
    command.upgrade(config, "head")

    assert rows(engine, "PRAGMA foreign_key_check") == []


def test_head_moves_derived_rows_into_normalized_addresses(old_database):
    config, engine = old_database
    command.upgrade(config, MID_REVISION)
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

    command.downgrade(config, MID_REVISION)
    addresses = by_id(engine, "SELECT * FROM addresses")
    assert addresses["d-ada-1"] == DERIVED_ROW
    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"]["address_id"] == "d-ada-1"
    # d-ada-1 edits its address, so it comes back as a derived row of its own.
    # Grace's print version copies hers verbatim and collapses into it.
    assert mailpieces["mp-grace"]["address_id"] == "v-grace-1"
    assert addresses["v-grace-1"]["derived_from_id"] is None
    assert addresses["v-grace-1"]["address_line1"] == "3 Mark II Lane"


def test_round_trip_from_head_to_the_recipient_schema_and_back(old_database):
    """The whole chain down to the recipients era and back up again. Verified
    users, their addresses, and their mail return row for row."""
    config, engine = old_database
    command.upgrade(config, "head")
    command.downgrade(config, OLD_REVISION)

    recipients = by_id(engine, "SELECT * FROM recipients", key="email")
    assert set(recipients) == {"ada@example.com", "grace@example.com"}
    assert recipients["ada@example.com"]["address_line1"] == "1 Ockham Park"
    assert recipients["ada@example.com"]["verified_at"] == DAY_1
    assert recipients["grace@example.com"]["unsubscribed_at"] == DAY_4

    versions = by_id(engine, "SELECT * FROM recipient_versions")
    assert set(versions) == {"v-ada-1", "v-ada-3", "v-grace-1"}
    assert versions["v-ada-1"] == RECIPIENT_VERSIONS[0]
    assert versions["v-grace-1"] == RECIPIENT_VERSIONS[3]

    mailpieces = by_id(engine, "SELECT * FROM mailpieces")
    assert mailpieces["mp-ada"]["recipient_version_id"] == "v-ada-1"
    assert mailpieces["mp-grace"] == MAILPIECES[1]

    assert rows(engine, "SELECT * FROM mailings") == MAILINGS
    assert rows(engine, "SELECT * FROM recipient_sessions") == RECIPIENT_SESSIONS

    command.upgrade(config, "head")
    assert rows(engine, "PRAGMA foreign_key_check") == []
    assert set(by_id(engine, "SELECT * FROM users", key="email")) == {
        "ada@example.com",
        "grace@example.com",
    }
    assert len(rows(engine, "SELECT * FROM mailpieces")) == 2


def test_downgrade_from_head_keeps_verified_users_only(old_database):
    config, engine = old_database
    command.upgrade(config, "head")
    command.downgrade(config, MID_REVISION)

    users = by_id(engine, "SELECT * FROM users", key="email")
    assert set(users) == {"ada@example.com", "grace@example.com"}
    assert users["ada@example.com"]["verified_at"] == DAY_1
    assert users["grace@example.com"]["verified_at"] == DAY_1
