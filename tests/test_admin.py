import re
from typing import get_args, get_type_hints

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bokehbowl.db import (
    ADDRESS_FIELDS,
    Address,
    AddressComponents,
    Edition,
    Mailpiece,
    NormalizedAddress,
    User,
)
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


def normalized_address_id_from(page_html: str) -> str:
    """The normalized_address_id the page's mark-sent form would submit."""
    return re.search(r'name="normalized_address_id" value="([^"]*)"', page_html).group(
        1
    )


def update_account(client, form: dict) -> None:
    csrf = csrf_from(client.get("/account").text)
    client.post("/account", data={"csrf": csrf, **form})


def normalize_current_address(client, csrf, **overrides) -> None:
    """Save a print version of the user's current address via the admin form."""
    with Session(client.app.state.engine) as db:
        address = db.scalars(
            select(Address).order_by(Address.created_at.desc(), Address.id.desc())
        ).first()
        form = {
            "name": address.addressee,
            "address_line1": address.address_line1,
            "address_line2": address.address_line2 or "",
            "city": address.city,
            "region": address.region or "",
            "postal_code": address.postal_code,
            "country": address.country,
        }
        address_id = address.id
    response = client.post(
        f"/admin/addresses/{address_id}/normalize",
        data={"csrf": csrf, **form, **overrides},
        follow_redirects=False,
    )
    assert response.status_code == 303


def submit_normalize_form_as_prefilled(client, address_id: str) -> None:
    """Save the admin normalize form back exactly as the page served it."""
    path = f"/admin/addresses/{address_id}/normalize"
    page = client.get(path).text
    start = page.index('<form class="form-stack"')
    form = page[start : page.index("</form>", start)]
    fields = dict(re.findall(r'name="([^"]+)"(?: value="([^"]*)")?', form))
    fields["country"] = re.search(
        r'<option value="([^"]+)" selected>', form
    ).group(1)
    response = client.post(path, data=fields, follow_redirects=False)
    assert response.status_code == 303


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
    assert response.headers["cache-control"] == "no-store"


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
    assert 'value="nope"' not in response.text
    assert 'autocomplete="current-password"' in response.text


def test_users_table_shows_db_columns(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    page = client.get("/admin?table=users")
    assert "<h1>Admin</h1>" in page.text
    assert "ada@example.com" in page.text
    for column in ["email", "unsubscribed_at", "created_at"]:
        assert f"<th>{column}</th>" in page.text


def test_users_table_shows_current_address_and_its_normalized_address(
    client, mailer
):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 3")
    update_account(client, OCKHAM_PARK)

    page = client.get("/admin?table=users").text
    assert "<th>current_address</th>" in page
    assert "<th>current_normalized_address</th>" in page
    assert "1 Ockham Park" in page
    assert "Flat 3" not in page

    normalize_current_address(client, csrf, address_line1="1 Ockham Park, Flat 4")
    page = client.get("/admin?table=users").text
    assert "1 Ockham Park, Flat 4" in page


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
    assert "current_address" not in page.text
    assert "Nothing here yet." in page.text


def test_editions_table_shows_sent_mailpiece_count(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={
            "csrf": csrf,
            "normalized_address_id": normalized_address_id_from(detail),
        },
    )

    table = client.get("/admin?table=editions").text
    assert "<th>sent_mailpieces</th>" in table
    assert re.search(r"<td>1</td>\s*<td>", table)


def test_deleting_an_edition_archives_it(client, mailer):
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf, title="temporary edition")
    edition_id = detail_url.rsplit("/", maxsplit=1)[-1]

    confirmation = client.get(f"{detail_url}/delete").text
    assert "Delete “temporary edition”?" in confirmation
    assert f'action="{detail_url}/delete"' in confirmation
    assert "Confirm delete" in confirmation
    with Session(client.app.state.engine) as db:
        assert db.get(Edition, edition_id).deleted_at is None

    response = client.post(
        f"{detail_url}/delete", data={"csrf": csrf}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin?table=editions"
    table = client.get("/admin?table=editions").text
    assert "temporary edition" in table
    assert "Deleted" in table
    assert f'href="{detail_url}"' not in table
    assert client.get(detail_url).status_code == 404

    with Session(client.app.state.engine) as db:
        edition = db.get(Edition, edition_id)
        assert edition is not None
        assert edition.deleted_at is not None


def test_mailpieces_table_renders_empty(client, mailer):
    admin_login(client)
    page = client.get("/admin?table=mailpieces")
    for column in ["edition_id", "user_id", "normalized_address_id"]:
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


def test_admin_unsubscribe_requires_confirmation(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    user_id = sole_user_id(client)

    confirmation = client.get(f"/admin/users/{user_id}/unsubscribe").text
    assert "Unsubscribe ada@example.com?" in confirmation
    assert "Confirm unsubscribe" in confirmation
    with Session(client.app.state.engine) as db:
        assert db.get(User, user_id).unsubscribed_at is None


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


def test_unsubscribed_user_can_log_in_and_resubscribe(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": admin_csrf})

    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    response = client.post(
        "/signup/verify",
        data={**SIGNUP_FORM, "csrf": csrf, "code": mailer.last_code()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(client.app.state.engine) as db:
        user = db.scalars(select(User)).one()
        assert user.unsubscribed_at is not None

    account_csrf = csrf_from(client.get("/account").text)
    client.post("/account/resubscribe", data={"csrf": account_csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalar(select(User.unsubscribed_at)) is None


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
    assert "Needs review (1)" in detail
    assert "To send (0)" in detail
    assert "Ada Lovelace" in detail

    normalize_current_address(client, csrf)
    detail = client.get(detail_url).text
    assert "Needs review" not in detail
    assert "To send (1)" in detail

    normalized_id = normalized_address_id_from(detail)
    client.post(
        f"{detail_url}/send/{user_id}",
        data={"csrf": csrf, "normalized_address_id": normalized_id},
    )
    detail = client.get(detail_url).text
    assert "To send (0)" in detail
    assert "Sent (1)" in detail

    client.post(
        f"{detail_url}/send/{user_id}",
        data={"csrf": csrf, "normalized_address_id": normalized_id},
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
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    normalized_id = normalized_address_id_from(client.get(detail_url).text)
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={"csrf": csrf, "normalized_address_id": normalized_id},
    )
    detail = client.get(detail_url).text
    assert "1 Ockham Park" in detail
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.normalized_address.address_line1 == "1 Ockham Park"


def test_sent_mailpiece_uses_complete_formatted_address(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(
        client,
        csrf,
        address_line2="Apartment 2B",
        region="Greater London",
    )
    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={
            "csrf": csrf,
            "normalized_address_id": normalized_address_id_from(detail),
        },
    )

    sent = client.get(detail_url).text
    assert "12 Analytical Way<br>Apartment 2B<br>" in sent
    assert "London, Greater London N1 9GU<br>" in sent


def test_to_send_labels_its_address_as_normalized(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)

    detail = client.get(create_edition(client, csrf)).text
    assert "<th>Normalized address</th>" in detail


def test_unsubscribed_excluded_from_edition_list(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "To send (0)" in detail
    assert "Needs review" not in detail


def test_signup_after_the_edition_is_left_off_it(client, mailer):
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)
    normalize_current_address(client, csrf)

    detail = client.get(detail_url).text
    assert "To send (0)" in detail
    assert "Needs review" not in detail

    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" not in labels.text

    later = create_edition(client, csrf)
    assert "To send (1)" in client.get(later).text


def test_mark_sent_rejects_unsubscribed_user(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    detail_url = create_edition(client, csrf)
    normalize_current_address(client, csrf)
    normalized_id = normalized_address_id_from(client.get(detail_url).text)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    response = client.post(
        f"{detail_url}/send/{user_id}",
        data={"csrf": csrf, "normalized_address_id": normalized_id},
    )
    assert response.status_code == 409
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_mark_sent_rejects_a_user_who_signed_up_after_the_edition(client, mailer):
    csrf = admin_login(client)
    earlier = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)
    normalize_current_address(client, csrf)
    later = create_edition(client, csrf)
    normalized_id = normalized_address_id_from(client.get(later).text)
    response = client.post(
        f"{earlier}/send/{sole_user_id(client)}",
        data={"csrf": csrf, "normalized_address_id": normalized_id},
    )
    assert response.status_code == 409
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_mark_sent_with_an_unknown_normalized_address_is_404(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    detail_url = create_edition(client, csrf)
    response = client.post(
        f"{detail_url}/send/{user_id}",
        data={"csrf": csrf, "normalized_address_id": "nope"},
    )
    assert response.status_code == 404
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_mark_sent_pins_the_form_the_page_named(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    printed = normalized_address_id_from(client.get(detail_url).text)
    update_account(client, OCKHAM_PARK)
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={"csrf": csrf, "normalized_address_id": printed},
    )
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.normalized_address_id == printed
        assert mailpiece.normalized_address.address_line1 == "12 Analytical Way"


def test_normalized_address_used_for_mailing_but_not_account_page(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 3")

    assert "Flat 3" not in client.get("/account").text

    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "Flat 3" in detail
    assert "Flat 3" in client.get(f"{detail_url}/labels.csv").text

    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={
            "csrf": csrf,
            "normalized_address_id": normalized_address_id_from(detail),
        },
    )
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.normalized_address.address_line1 == "12 Analytical Way, Flat 3"
        assert mailpiece.normalized_address.address.address_line1 == "12 Analytical Way"
    assert "12 Analytical Way, Flat 3" in client.get(detail_url).text


def test_approve_files_the_shown_address_as_the_print_version(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "Needs review (1)" in detail

    action = re.search(
        r'<form method="post" action="(/admin/addresses/[^"]+/normalize)"', detail
    ).group(1)
    fields = dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', detail)
    )
    response = client.post(action, data=fields, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == detail_url

    with Session(client.app.state.engine) as db:
        normalized_address = db.scalars(select(NormalizedAddress)).one()
        assert normalized_address.addressee == "Ada Lovelace"
        assert normalized_address.address_line1 == "12 Analytical Way"
        assert normalized_address.postal_code == "N1 9GU"
    detail = client.get(detail_url).text
    assert "Needs review" not in detail
    assert "To send (1)" in detail


def test_mark_sent_with_a_normalized_address_of_another_users_address_is_404(
    client, mailer
):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/").text)
    client.post("/logout", data={"csrf": csrf})
    csrf = csrf_from(client.get("/").text)
    grace = {**SIGNUP_FORM, "email": "grace@example.com", "name": "Grace Hopper"}
    client.post("/signup", data={**grace, "csrf": csrf})
    client.post(
        "/signup/verify",
        data={**grace, "csrf": csrf, "code": mailer.last_code()},
        follow_redirects=False,
    )

    admin_csrf = admin_login(client)
    with Session(client.app.state.engine) as db:
        ada_id = db.scalars(
            select(User.id).where(User.email == "ada@example.com")
        ).one()
        grace_address_id = db.scalars(
            select(Address.id).join(User).where(User.email == "grace@example.com")
        ).one()
    response = client.post(
        f"/admin/addresses/{grace_address_id}/normalize",
        data={
            "csrf": admin_csrf,
            "name": "Grace Hopper",
            "address_line1": "3 Mark II Lane",
            "address_line2": "",
            "city": "Arlington",
            "region": "VA",
            "postal_code": "22201",
            "country": "United States",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(client.app.state.engine) as db:
        graces_normalized_address = db.scalars(select(NormalizedAddress.id)).one()

    detail_url = create_edition(client, admin_csrf)
    response = client.post(
        f"{detail_url}/send/{ada_id}",
        data={"csrf": admin_csrf, "normalized_address_id": graces_normalized_address},
    )
    assert response.status_code == 404
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_new_address_supersedes_the_old_rows_normalized_address(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 3")
    update_account(client, OCKHAM_PARK)

    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "Needs review (1)" in detail
    assert "1 Ockham Park" in detail
    labels = client.get(f"{detail_url}/labels.csv").text
    assert "Ockham" not in labels
    assert "Flat 3" not in labels


def test_renormalizing_appends_and_the_latest_normalized_address_wins(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 3")
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 4")

    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(NormalizedAddress)).all()) == 2

    detail_url = create_edition(client, csrf)
    labels = client.get(f"{detail_url}/labels.csv")
    assert labels.headers["cache-control"] == "no-store"
    assert "Flat 4" in labels.text
    assert "Flat 3" not in labels.text


def test_the_address_components_value_matches_the_stored_columns():
    """AddressComponents is what the two address tables compare and store: its
    fields are exactly the address columns they carry, and the fields it marks
    optional are the columns that are nullable."""
    optional = {
        name
        for name, hint in get_type_hints(AddressComponents).items()
        if type(None) in get_args(hint)
    }
    for table in (Address, NormalizedAddress):
        columns = {column.name: column for column in table.__table__.columns}
        stored = set(columns) - {"id", "user_id", "address_id", "created_at"}
        assert set(ADDRESS_FIELDS) == stored
        assert optional == {name for name in stored if columns[name].nullable}


def test_saving_the_normalize_form_untouched_appends_no_print_version(client, mailer):
    """Approve files the address as entered. Opening Normalize afterwards
    prefills that print version, so saving it untouched submits what is already
    on file."""
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    with Session(client.app.state.engine) as db:
        address_id = db.scalars(select(Address.id)).one()

    submit_normalize_form_as_prefilled(client, address_id)

    with Session(client.app.state.engine) as db:
        filed = db.scalars(select(NormalizedAddress)).one()
        assert filed.address_line1 == "12 Analytical Way"


def test_normalize_route_unknown_address_is_404(client, mailer):
    csrf = admin_login(client)
    path = "/admin/addresses/nope/normalize"
    assert client.get(path).status_code == 404
    response = client.post(
        path,
        data={"csrf": csrf, **{field: "x" for field in OCKHAM_PARK}},
    )
    assert response.status_code == 404


def test_labels_csv_lists_pending_only(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" in labels.text
    client.post(
        f"{detail_url}/send/{sole_user_id(client)}",
        data={
            "csrf": csrf,
            "normalized_address_id": normalized_address_id_from(
                client.get(detail_url).text
            ),
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
    assert header.startswith("id,email,unsubscribed_at")
    assert "ada@example.com" in response.text
    addresses = client.get("/admin/export.csv?table=addresses")
    assert "postal_code" in addresses.text.splitlines()[0]


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


def test_the_database_admits_one_mailpiece_per_user_per_edition(client, mailer):
    """Two clicks can both read the edition as unsent before either writes. The
    UNIQUE constraint is what holds the second one out."""
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    user_id = sole_user_id(client)
    normalized_id = normalized_address_id_from(client.get(detail_url).text)
    client.post(
        f"{detail_url}/send/{user_id}",
        data={"csrf": csrf, "normalized_address_id": normalized_id},
    )

    with Session(client.app.state.engine) as db:
        sent = db.scalars(select(Mailpiece)).one()
        db.add(
            Mailpiece(
                edition_id=sent.edition_id,
                user_id=sent.user_id,
                normalized_address_id=sent.normalized_address_id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()

    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(Mailpiece)).all()) == 1
