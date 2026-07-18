from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from bokehbowl.db import Mailpiece, Recipient, RecipientVersion, utcnow
from tests.conftest import ADMIN_PASSWORD, csrf_from, sign_up_and_verify


def sole_recipient_id(client) -> str:
    with Session(client.app.state.engine) as db:
        return db.scalars(select(Recipient.id)).one()


def sole_mailpiece_id(client) -> str:
    with Session(client.app.state.engine) as db:
        return db.scalars(select(Mailpiece.id)).one()


def admin_login(client) -> str:
    csrf = csrf_from(client.get("/admin/login").text)
    response = client.post(
        "/admin/login",
        data={"csrf": csrf, "password": ADMIN_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return csrf


def test_dashboard_requires_login(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_cookie_replay_rejected_after_logout(client):
    csrf = admin_login(client)
    saved = dict(client.cookies)
    logout = client.post("/admin/logout", data={"csrf": csrf}, follow_redirects=False)
    assert logout.status_code == 303
    client.cookies = saved
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_wrong_password_rejected(client):
    csrf = csrf_from(client.get("/admin/login").text)
    response = client.post("/admin/login", data={"csrf": csrf, "password": "nope"})
    assert response.status_code == 401


def test_login_throttled_after_repeated_failures(client):
    csrf = csrf_from(client.get("/admin/login").text)
    for _ in range(10):
        response = client.post("/admin/login", data={"csrf": csrf, "password": "nope"})
        assert response.status_code == 401
    response = client.post(
        "/admin/login", data={"csrf": csrf, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 429
    assert "Too many attempts" in response.text


def test_throttle_is_per_client_address(client):
    csrf = csrf_from(client.get("/admin/login").text)
    for _ in range(10):
        response = client.post("/admin/login", data={"csrf": csrf, "password": "nope"})
        assert response.status_code == 401
    response = client.post(
        "/admin/login", data={"csrf": csrf, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 429

    with TestClient(
        client.app, base_url="https://testserver", client=("10.9.8.7", 999)
    ) as other:
        other_csrf = csrf_from(other.get("/admin/login").text)
        response = other.post(
            "/admin/login",
            data={"csrf": other_csrf, "password": ADMIN_PASSWORD},
            follow_redirects=False,
        )
        assert response.status_code == 303


def test_backstop_throttles_across_addresses(client):
    now = utcnow()
    client.app.state.admin_login_throttle.failures = {
        str(index): [now] for index in range(100)
    }
    csrf = csrf_from(client.get("/admin/login").text)
    response = client.post(
        "/admin/login", data={"csrf": csrf, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 429
    assert "Too many attempts" in response.text


def test_recipients_table_shows_db_columns(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    page = client.get("/admin?table=recipients")
    assert "Ada Lovelace" in page.text
    assert "ada@example.com" in page.text
    for column in ["email", "verified_at", "unsubscribed_at", "created_at"]:
        assert f"<th>{column}</th>" in page.text


def test_unknown_table_is_404(client, mailer):
    admin_login(client)
    assert client.get("/admin?table=login_codes").status_code == 404
    assert client.get("/admin?table=nope").status_code == 404


def test_signup_records_first_version(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    page = client.get("/admin?table=recipient_versions")
    assert "12 Analytical Way" in page.text


def test_account_update_appends_version_and_keeps_old(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    client.post(
        "/account",
        data={
            "csrf": csrf,
            "name": "Ada Lovelace",
            "address_line1": "1 Ockham Park",
            "address_line2": "",
            "city": "Surrey",
            "region": "",
            "postal_code": "GU23 6NQ",
            "country": "United Kingdom",
        },
    )
    admin_login(client)
    page = client.get("/admin?table=recipient_versions")
    assert "12 Analytical Way" in page.text
    assert "1 Ockham Park" in page.text
    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(RecipientVersion)).all()) == 2


def test_unchanged_save_appends_no_version(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    client.post(
        "/account",
        data={
            "csrf": csrf,
            "name": "Ada Lovelace",
            "address_line1": "12 Analytical Way",
            "address_line2": "",
            "city": "London",
            "region": "",
            "postal_code": "N1 9GU",
            "country": "United Kingdom",
        },
    )
    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(RecipientVersion)).all()) == 1


def test_mailings_table_renders_empty(client, mailer):
    admin_login(client)
    page = client.get("/admin?table=mailings")
    assert "<th>title</th>" in page.text
    assert "Nothing here yet." in page.text


def test_mailpieces_table_renders_empty(client, mailer):
    admin_login(client)
    page = client.get("/admin?table=mailpieces")
    for column in ["mailing_id", "recipient_id", "recipient_version_id", "sent_at"]:
        assert f"<th>{column}</th>" in page.text
    assert "Nothing here yet." in page.text


def test_admin_unregister_is_soft_and_idempotent(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    recipient_id = sole_recipient_id(client)
    client.post(f"/admin/recipients/{recipient_id}/unregister", data={"csrf": csrf})
    with Session(client.app.state.engine) as db:
        first = db.scalar(select(Recipient.unsubscribed_at))
        assert first is not None
    client.post(f"/admin/recipients/{recipient_id}/unregister", data={"csrf": csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalar(select(Recipient.unsubscribed_at)) == first


def test_admin_reregister(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    recipient_id = sole_recipient_id(client)
    client.post(f"/admin/recipients/{recipient_id}/unregister", data={"csrf": csrf})
    page = client.get("/admin?table=recipients").text
    assert "Reregister" in page and "Unregister" not in page
    client.post(f"/admin/recipients/{recipient_id}/reregister", data={"csrf": csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalar(select(Recipient.unsubscribed_at)) is None
    page = client.get("/admin?table=recipients").text
    assert "Unregister" in page


def create_mailing(client, csrf, title="sailboat postcard") -> str:
    response = client.post(
        "/admin/mailings", data={"csrf": csrf, "title": title}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"]


def test_mailing_workflow(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    recipient_id = sole_recipient_id(client)
    detail_url = create_mailing(client, csrf)

    detail = client.get(detail_url).text
    assert 'class=" admin"' in detail
    assert "To send (1)" in detail
    assert "Ada Lovelace" in detail

    client.post(f"{detail_url}/send/{recipient_id}", data={"csrf": csrf})
    detail = client.get(detail_url).text
    assert "To send (0)" in detail
    assert "Sent (1)" in detail

    client.post(f"{detail_url}/send/{recipient_id}", data={"csrf": csrf})
    detail = client.get(detail_url).text
    assert "Sent (1)" in detail

    mailpiece_id = sole_mailpiece_id(client)
    client.post(f"/admin/mailpieces/{mailpiece_id}/delete", data={"csrf": csrf})
    detail = client.get(detail_url).text
    assert "To send (1)" in detail
    assert "Sent (0)" in detail


def test_mailpiece_pins_current_address_version(client, mailer):
    sign_up_and_verify(client, mailer)
    account_csrf = csrf_from(client.get("/account").text)
    client.post(
        "/account",
        data={
            "csrf": account_csrf,
            "name": "Ada Lovelace",
            "address_line1": "1 Ockham Park",
            "address_line2": "",
            "city": "Surrey",
            "region": "",
            "postal_code": "GU23 6NQ",
            "country": "United Kingdom",
        },
    )
    csrf = admin_login(client)
    detail_url = create_mailing(client, csrf)
    client.post(f"{detail_url}/send/{sole_recipient_id(client)}", data={"csrf": csrf})
    detail = client.get(detail_url).text
    assert "1 Ockham Park" in detail
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.recipient_version.address_line1 == "1 Ockham Park"


def test_unregistered_excluded_from_mailing_list(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    recipient_id = sole_recipient_id(client)
    client.post(f"/admin/recipients/{recipient_id}/unregister", data={"csrf": csrf})
    detail_url = create_mailing(client, csrf)
    assert "To send (0)" in client.get(detail_url).text


def test_late_signup_excluded_from_default_list_but_sendable(client, mailer):
    csrf = admin_login(client)
    detail_url = create_mailing(client, csrf)
    sign_up_and_verify(client, mailer)

    detail = client.get(detail_url).text
    assert "To send (0)" in detail
    assert "Signed up after this mailing (1)" in detail

    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" not in labels.text

    client.post(f"{detail_url}/send/{sole_recipient_id(client)}", data={"csrf": csrf})
    detail = client.get(detail_url).text
    assert "Sent (1)" in detail
    assert "Signed up after this mailing" not in detail


def test_mark_sent_rejects_unregistered_recipient(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    recipient_id = sole_recipient_id(client)
    detail_url = create_mailing(client, csrf)
    client.post(f"/admin/recipients/{recipient_id}/unregister", data={"csrf": csrf})
    response = client.post(f"{detail_url}/send/{recipient_id}", data={"csrf": csrf})
    assert response.status_code == 409
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_labels_csv_lists_pending_only(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    detail_url = create_mailing(client, csrf)
    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" in labels.text
    client.post(f"{detail_url}/send/{sole_recipient_id(client)}", data={"csrf": csrf})
    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" not in labels.text


def test_csv_export_matches_table(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    response = client.get("/admin/export.csv?table=recipients")
    assert response.status_code == 200
    header = response.text.splitlines()[0]
    assert header.startswith("id,email,name,address_line1")
    assert "ada@example.com" in response.text
    versions = client.get("/admin/export.csv?table=recipient_versions")
    assert "valid_from" in versions.text.splitlines()[0]


def test_csv_export_neutralizes_formula_cells(client, mailer):
    sign_up_and_verify(client, mailer)
    with Session(client.app.state.engine) as db:
        recipient = db.scalars(select(Recipient)).one()
        recipient.name = '=HYPERLINK("https://evil.example",1)'
        db.commit()
    admin_login(client)
    response = client.get("/admin/export.csv?table=recipients")
    assert response.status_code == 200
    assert "'=HYPERLINK" in response.text
    assert ",=HYPERLINK" not in response.text
