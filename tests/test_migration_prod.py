"""Migration rehearsal against a production database dump.

Skipped unless BOKEHBOWL_PROD_DUMP points at a copy of the production SQLite
file (see README). The dump itself is never mutated: the test migrates a copy.
"""

import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from bokehbowl.app import create_app
from bokehbowl.config import AppConfig, ConsoleMail
from bokehbowl.mailer import build_mailer
from tests.conftest import ADMIN_PASSWORD, csrf_from


ROOT = Path(__file__).resolve().parent.parent
DUMP = os.environ.get("BOKEHBOWL_PROD_DUMP")

pytestmark = pytest.mark.skipif(
    not DUMP or not Path(DUMP).is_file(),
    reason="set BOKEHBOWL_PROD_DUMP to a production database copy",
)

LABEL_FIELDS = (
    "name",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
)

ADDRESS_FIELDS = ("addressee",) + LABEL_FIELDS[1:]


def rows(db: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return db.execute(query).fetchall()


def snapshot_before(db: sqlite3.Connection) -> dict:
    names = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in names}
    if "recipients" not in tables:
        pytest.fail("dump is not at the pre-migration schema (no recipients table)")
    return {
        "recipients": rows(db, "SELECT * FROM recipients"),
        "versions": rows(
            db,
            "SELECT * FROM recipient_versions ORDER BY recipient_id, valid_from, id",
        ),
        "mailings": rows(db, "SELECT * FROM mailings"),
        "mailpieces": rows(db, "SELECT * FROM mailpieces"),
        "sessions": rows(db, "SELECT * FROM recipient_sessions"),
    }


def version_runs(versions: list[sqlite3.Row]) -> dict[str, list[tuple]]:
    """Distinct consecutive label states per recipient, in order."""
    runs: dict[str, list[tuple]] = {}
    for version in versions:
        fields = tuple(version[field] for field in LABEL_FIELDS)
        history = runs.setdefault(version["recipient_id"], [])
        if not history or history[-1] != fields:
            history.append(fields)
    return runs


def migrate(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head")


def test_prod_dump_migrates_cleanly(monkeypatch, tmp_path):
    db_path = tmp_path / "prod-copy.db"
    shutil.copy(DUMP, db_path)

    before_db = sqlite3.connect(db_path)
    before_db.row_factory = sqlite3.Row
    before = snapshot_before(before_db)
    before_db.close()

    migrate(db_path, monkeypatch)

    after_db = sqlite3.connect(db_path)
    after_db.row_factory = sqlite3.Row

    users = {row["id"]: row for row in rows(after_db, "SELECT * FROM users")}
    assert set(users) == {row["id"] for row in before["recipients"]}
    for old in before["recipients"]:
        user = users[old["id"]]
        assert user["email"] == old["email"]
        assert user["created_at"] == old["created_at"]
        assert user["verified_at"] == old["verified_at"]
        assert user["unsubscribed_at"] == old["unsubscribed_at"]
        expected = (
            "pending"
            if old["verified_at"] is None
            else "unsubscribed"
            if old["unsubscribed_at"] is not None
            else "active"
        )
        assert user["status"] == expected

    addresses = rows(
        after_db, "SELECT * FROM addresses ORDER BY user_id, created_at, id"
    )
    assert all(row["derived_from_id"] is None for row in addresses)
    by_user: dict[str, list[sqlite3.Row]] = {}
    for address in addresses:
        by_user.setdefault(address["user_id"], []).append(address)
    runs = version_runs(before["versions"])
    assert set(by_user) == set(runs)
    for user_id, history in runs.items():
        actual = [
            tuple(address[field] for field in ADDRESS_FIELDS)
            for address in by_user[user_id]
        ]
        assert actual == history

    for old in before["recipients"]:
        latest = by_user[old["id"]][-1]
        for old_field, new_field in zip(LABEL_FIELDS, ADDRESS_FIELDS, strict=True):
            assert latest[new_field] == old[old_field]

    editions = {row["id"]: row for row in rows(after_db, "SELECT * FROM editions")}
    assert set(editions) == {row["id"] for row in before["mailings"]}
    assert all(row["status"] == "open" for row in editions.values())

    address_by_id = {row["id"]: row for row in addresses}
    versions_by_id = {row["id"]: row for row in before["versions"]}
    mailpieces = rows(after_db, "SELECT * FROM mailpieces")
    assert len(mailpieces) == len(before["mailpieces"])
    for old in before["mailpieces"]:
        (new,) = [m for m in mailpieces if m["id"] == old["id"]]
        assert new["edition_id"] == old["mailing_id"]
        assert new["user_id"] == old["recipient_id"]
        assert new["sent_at"] == old["sent_at"]
        version = versions_by_id[old["recipient_version_id"]]
        address = address_by_id[new["address_id"]]
        for old_field, new_field in zip(LABEL_FIELDS, ADDRESS_FIELDS, strict=True):
            assert address[new_field] == version[old_field]

    sessions = rows(after_db, "SELECT * FROM user_sessions")
    assert {s["token"] for s in sessions} == {s["token"] for s in before["sessions"]}

    assert after_db.execute("PRAGMA foreign_key_check").fetchall() == []
    after_db.close()


def test_migrated_prod_dump_serves_admin_pages(monkeypatch, tmp_path):
    db_path = tmp_path / "prod-copy.db"
    shutil.copy(DUMP, db_path)
    migrate(db_path, monkeypatch)

    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    config = AppConfig(
        database_url=f"sqlite:///{db_path}",
        session_secret="test-secret",
        admin_password=ADMIN_PASSWORD,
        cookie_secure=True,
        mail=ConsoleMail(),
        operator_name="Testy Operator",
        operator_email="operator@example.com",
        notify_email="notify@example.com",
        commit="abc1234def5678",
    )
    app = create_app(config=config, engine=engine, mailer=build_mailer(config.mail))
    with TestClient(app, base_url="https://testserver") as client:
        csrf = csrf_from(client.get("/admin/login").text)
        response = client.post(
            "/admin/login", data={"csrf": csrf, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 200
        for table in ("users", "addresses", "editions", "mailpieces"):
            assert client.get(f"/admin?table={table}").status_code == 200
            export = client.get(f"/admin/export.csv?table={table}")
            assert export.status_code == 200
        with sqlite3.connect(db_path) as db:
            edition = db.execute("SELECT id FROM editions LIMIT 1").fetchone()
        if edition is not None:
            detail = client.get(f"/admin/editions/{edition[0]}")
            assert detail.status_code == 200
            labels = client.get(f"/admin/editions/{edition[0]}/labels.csv")
            assert labels.status_code == 200
