import re

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from bokehbowl.db import Address, Mailpiece, User, utcnow
from tests.conftest import ADMIN_PASSWORD, SIGNUP_FORM, csrf_from, sign_up_and_verify


OCKHAM_PARK = {
    "name": "Ada Lovelace",
    "address_line1": "1 Ockham Park",
    "address_line2": "",
    "city": "Surrey",
    "region": "",
    "postal_code": "GU23 6NQ",
    "country": "United Kingdom",
}


def sole_user_id(client) -> str:
    with Session(client.app.state.engine) as db:
        return db.scalars(select(User.id)).one()


def sole_mailpiece_id(client) -> str:
    with Session(client.app.state.engine) as db:
        return db.scalars(select(Mailpiece.id)).one()


def address_id_from(page_html: str) -> str:
    """The address_id the page's mark-sent form would submit."""
    return re.search(r'name="address_id" value="([^"]+)"', page_html).group(1)


def update_account(client, form: dict) -> None:
    csrf = csrf_from(client.get("/account").text)
    client.post("/account", data={"csrf": csrf, **form})


def validated_copy_of(manual: Address, **overrides) -> Address:
    values = {
        "user_id": manual.user_id,
        "addressee": manual.addressee,
        "address_line1": manual.address_line1,
        "address_line2": manual.address_line2,
        "city": manual.city,
        "region": manual.region,
        "postal_code": manual.postal_code,
        "country": manual.country,
        "derived_from_id": manual.id,
    }
    return Address(**(values | overrides))


def validate_sole_address(client, **overrides) -> None:
    """Attach a validated correction to the user's signup address."""
    with Session(client.app.state.engine) as db:
        manual = db.scalars(select(Address)).one()
        db.add(validated_copy_of(manual, **overrides))
        db.commit()


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


def test_users_table_shows_db_columns(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    page = client.get("/admin?table=users")
    assert "<h1>Admin</h1>" in page.text
    assert "ada@example.com" in page.text
    for column in ["email", "verified_at", "unsubscribed_at", "created_at"]:
        assert f"<th>{column}</th>" in page.text


def test_unknown_table_is_404(client, mailer):
    admin_login(client)
    assert client.get("/admin?table=login_codes").status_code == 404
    assert client.get("/admin?table=nope").status_code == 404


def test_signup_records_first_address(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    page = client.get("/admin?table=addresses")
    assert "Ada Lovelace" in page.text
    assert "12 Analytical Way" in page.text


def test_account_update_appends_address_and_keeps_old(client, mailer):
    sign_up_and_verify(client, mailer)
    update_account(client, OCKHAM_PARK)
    admin_login(client)
    page = client.get("/admin?table=addresses")
    assert "12 Analytical Way" in page.text
    assert "1 Ockham Park" in page.text
    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(Address)).all()) == 2


def test_unchanged_save_appends_no_address(client, mailer):
    sign_up_and_verify(client, mailer)
    update_account(client, SIGNUP_FORM)
    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(Address)).all()) == 1


def test_editions_table_renders_empty(client, mailer):
    admin_login(client)
    page = client.get("/admin?table=editions")
    assert "<th>title</th>" in page.text
    assert "Nothing here yet." in page.text


def test_mailpieces_table_renders_empty(client, mailer):
    admin_login(client)
    page = client.get("/admin?table=mailpieces")
    for column in ["edition_id", "user_id", "address_id", "sent_at"]:
        assert f"<th>{column}</th>" in page.text
    assert "Nothing here yet." in page.text


def test_admin_unsubscribe_is_soft_and_idempotent(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    with Session(client.app.state.engine) as db:
        first = db.scalar(select(User.unsubscribed_at))
        assert first is not None
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalar(select(User.unsubscribed_at)) == first


def test_admin_resubscribe(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    page = client.get("/admin?table=users").text
    assert "Resubscribe" in page and "Unsubscribe" not in page
    client.post(f"/admin/users/{user_id}/resubscribe", data={"csrf": csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalar(select(User.unsubscribed_at)) is None
    page = client.get("/admin?table=users").text
    assert "Unsubscribe" in page


def test_resubscribe_keeps_unverified_user_unverified(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    admin_csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": admin_csrf})
    client.post(f"/admin/users/{user_id}/resubscribe", data={"csrf": admin_csrf})
    with Session(client.app.state.engine) as db:
        user = db.scalars(select(User)).one()
        assert user.verified_at is None
        assert user.unsubscribed_at is None


def test_verify_while_unsubscribed_records_verification(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    admin_csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": admin_csrf})

    response = client.post(
        "/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(client.app.state.engine) as db:
        user = db.scalars(select(User)).one()
        assert user.verified_at is not None
        assert user.unsubscribed_at is not None

    account_csrf = csrf_from(client.get("/account").text)
    client.post("/account/resubscribe", data={"csrf": account_csrf})
    with Session(client.app.state.engine) as db:
        user = db.scalars(select(User)).one()
        assert user.verified_at is not None
        assert user.unsubscribed_at is None


def create_edition(client, csrf, title="sailboat postcard") -> str:
    response = client.post(
        "/admin/editions", data={"csrf": csrf, "title": title}, follow_redirects=False
    )
    assert response.status_code == 303
    return response.headers["location"]


def test_edition_workflow(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    detail_url = create_edition(client, csrf)

    detail = client.get(detail_url).text
    assert 'class="admin"' in detail
    assert "<h1>sailboat postcard</h1>" in detail
    assert "<h2>To send (1)</h2>" in detail
    assert "To send (1)" in detail
    assert "Ada Lovelace" in detail

    address_id = address_id_from(detail)
    client.post(
        f"{detail_url}/send/{user_id}", data={"csrf": csrf, "address_id": address_id}
    )
    detail = client.get(detail_url).text
    assert "To send (0)" in detail
    assert "Sent (1)" in detail

    client.post(
        f"{detail_url}/send/{user_id}", data={"csrf": csrf, "address_id": address_id}
    )
    detail = client.get(detail_url).text
    assert "Sent (1)" in detail

    mailpiece_id = sole_mailpiece_id(client)
    client.post(f"/admin/mailpieces/{mailpiece_id}/delete", data={"csrf": csrf})
    detail = client.get(detail_url).text
    assert "To send (1)" in detail
    assert "Sent (0)" in detail


def test_mailpiece_pins_current_address(client, mailer):
    sign_up_and_verify(client, mailer)
    update_account(client, OCKHAM_PARK)
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    address_id = address_id_from(client.get(detail_url).text)
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={"csrf": csrf, "address_id": address_id},
    )
    detail = client.get(detail_url).text
    assert "1 Ockham Park" in detail
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.address.address_line1 == "1 Ockham Park"


def test_unsubscribed_excluded_from_edition_list(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    detail_url = create_edition(client, csrf)
    assert "To send (0)" in client.get(detail_url).text


def test_late_signup_excluded_from_default_list_but_sendable(client, mailer):
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)

    detail = client.get(detail_url).text
    assert "To send (0)" in detail
    assert "Signed up after this edition (1)" in detail

    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" not in labels.text

    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={"csrf": csrf, "address_id": address_id_from(detail)},
    )
    detail = client.get(detail_url).text
    assert "Sent (1)" in detail
    assert "Signed up after this edition" not in detail


def test_mark_sent_rejects_unsubscribed_user(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    detail_url = create_edition(client, csrf)
    address_id = address_id_from(client.get(detail_url).text)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    response = client.post(
        f"{detail_url}/send/{user_id}", data={"csrf": csrf, "address_id": address_id}
    )
    assert response.status_code == 409
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_mark_sent_without_address_is_404(client, mailer):
    sign_up_and_verify(client, mailer)
    with Session(client.app.state.engine) as db:
        deleted_id = db.scalars(select(Address.id)).one()
        db.execute(delete(Address))
        db.commit()
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    detail_url = create_edition(client, csrf)
    assert "To send (0)" in client.get(detail_url).text
    response = client.post(
        f"{detail_url}/send/{user_id}", data={"csrf": csrf, "address_id": deleted_id}
    )
    assert response.status_code == 404


def test_mark_sent_pins_the_address_the_form_named(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    printed = address_id_from(client.get(detail_url).text)
    update_account(client, OCKHAM_PARK)
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={"csrf": csrf, "address_id": printed},
    )
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.address_id == printed
        assert mailpiece.address.address_line1 == "12 Analytical Way"


def test_validated_address_used_for_labels_but_not_displayed(client, mailer):
    sign_up_and_verify(client, mailer)
    validate_sole_address(client, address_line1="12 Analytical Way, Flat 3")

    assert "Flat 3" not in client.get("/account").text

    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "Flat 3" in detail
    assert "Flat 3" in client.get(f"{detail_url}/labels.csv").text

    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={"csrf": csrf, "address_id": address_id_from(detail)},
    )
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.address.address_line1 == "12 Analytical Way, Flat 3"
        assert mailpiece.address.derived_from_id is not None


def test_new_manual_address_supersedes_validation(client, mailer):
    sign_up_and_verify(client, mailer)
    validate_sole_address(client, address_line1="12 Analytical Way, Flat 3")
    update_account(client, OCKHAM_PARK)

    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    labels = client.get(f"{detail_url}/labels.csv").text
    assert "1 Ockham Park" in labels
    assert "Flat 3" not in labels


def test_validation_finishing_after_a_new_manual_address_stays_unused(client, mailer):
    sign_up_and_verify(client, mailer)
    with Session(client.app.state.engine) as db:
        original_id = db.scalars(select(Address.id)).one()
    update_account(client, OCKHAM_PARK)
    with Session(client.app.state.engine) as db:
        original = db.get(Address, original_id)
        db.add(validated_copy_of(original, address_line1="12 Analytical Way, Flat 3"))
        db.commit()

    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    labels = client.get(f"{detail_url}/labels.csv").text
    assert "1 Ockham Park" in labels
    assert "Flat 3" not in labels


def test_labels_csv_lists_pending_only(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" in labels.text
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={
            "csrf": csrf,
            "address_id": address_id_from(client.get(detail_url).text),
        },
    )
    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" not in labels.text


def test_csv_export_matches_table(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    response = client.get("/admin/export.csv?table=users")
    assert response.status_code == 200
    header = response.text.splitlines()[0]
    assert header.startswith("id,email,verified_at")
    assert "ada@example.com" in response.text
    addresses = client.get("/admin/export.csv?table=addresses")
    assert "derived_from_id" in addresses.text.splitlines()[0]


def test_csv_export_neutralizes_formula_cells(client, mailer):
    sign_up_and_verify(client, mailer)
    with Session(client.app.state.engine) as db:
        address = db.scalars(select(Address)).one()
        address.addressee = '=HYPERLINK("https://evil.example",1)'
        db.commit()
    admin_login(client)
    response = client.get("/admin/export.csv?table=addresses")
    assert response.status_code == 200
    assert "'=HYPERLINK" in response.text
    assert ",=HYPERLINK" not in response.text
