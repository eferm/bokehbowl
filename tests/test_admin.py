import re
from dataclasses import fields
from datetime import date, datetime
from typing import get_args, get_type_hints

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import bokehbowl.admin as admin
from bokehbowl.db import (
    Address,
    AddressComponents,
    Edition,
    Mailpiece,
    MailpieceDraft,
    MailReturn,
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


def sole_draft_id(client) -> str:
    with Session(client.app.state.engine) as db:
        return db.scalars(select(MailpieceDraft.id)).one()


def mark_sole_draft_sent(client, csrf, detail_url, *, follow_redirects=True):
    return client.post(
        f"{detail_url}/drafts/{sole_draft_id(client)}/mark-sent",
        data={"csrf": csrf},
        follow_redirects=follow_redirects,
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
    fields["country"] = re.search(r'<option value="([^"]+)" selected>', form).group(1)
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


def test_users_table_shows_current_address_and_its_normalized_address(client, mailer):
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
    mark_sole_draft_sent(client, csrf, detail_url)

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
    assert "Confirm Delete" in confirmation
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
    for column in [
        "edition_id",
        "user_id",
        "normalized_address_id",
    ]:
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
    assert "Confirm Unsubscribe" in confirmation
    assert "existing sessions will remain active" in confirmation
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


def test_admin_unsubscribe_preserves_the_user_session(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": admin_csrf})

    account = client.get("/account")
    assert account.status_code == 200
    assert "currently <strong>unsubscribed</strong>" in account.text
    with Session(client.app.state.engine) as db:
        user = db.scalars(select(User)).one()
        assert user.unsubscribed_at is not None

    account_csrf = csrf_from(account.text)
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
    detail_url = create_edition(client, csrf)

    detail = client.get(detail_url).text
    assert 'class="admin"' in detail
    assert "<h1>sailboat postcard</h1>" in detail
    assert "<h2>Mailpieces</h2>" in detail
    assert "Needs review" in detail
    assert "Ada Lovelace" in detail
    assert "Normalize" in detail
    assert "Remove" in detail
    assert "Mark Sent" not in detail

    normalize_current_address(client, csrf)
    detail = client.get(detail_url).text
    assert "Needs review" not in detail
    assert "Ready" in detail
    assert "Export Ready Addresses" in detail
    assert "Mark Sent" in detail

    draft_id = sole_draft_id(client)
    with Session(client.app.state.engine) as db:
        stale_draft = db.get(MailpieceDraft, draft_id)
        stale_edition = stale_draft.edition
        _ = stale_draft.user.sendable_normalized_address
        db.expunge_all()
    mark_sole_draft_sent(client, csrf, detail_url)
    detail = client.get(detail_url).text
    assert "Sent " in detail
    assert "Mark Returned" in detail
    assert "Mark Sent" not in detail
    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(Mailpiece)).all()) == 1
        assert db.scalars(select(MailpieceDraft)).all() == []

    with Session(client.app.state.engine) as db:
        with pytest.raises(HTTPException) as conflict:
            admin.mark_sent(
                Request({"type": "http", "app": client.app}),
                db,
                stale_edition,
                stale_draft,
            )
        assert conflict.value.status_code == 409

    repeated = client.post(
        f"{detail_url}/drafts/{draft_id}/mark-sent",
        data={"csrf": csrf},
    )
    assert repeated.status_code == 404

    mailpiece_id = sole_mailpiece_id(client)
    client.post(f"/admin/mailpieces/{mailpiece_id}/mark-returned", data={"csrf": csrf})
    with Session(client.app.state.engine) as db:
        db.scalars(select(MailReturn)).one().received_at = datetime(2026, 8, 4, 0, 30)
        db.commit()
    detail = client.get(detail_url).text
    assert "Returned 2026-08-03" in detail
    assert "Needs review" in detail
    assert "Mark Sent" not in detail
    assert "We couldn't deliver your last piece of mail" in client.get("/account").text
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(MailReturn)).one().mailpiece_id == mailpiece_id
        assert len(db.scalars(select(MailpieceDraft)).all()) == 1

    normalize_current_address(client, csrf)
    assert "Needs review" in client.get(detail_url).text
    assert "We couldn't deliver your last piece of mail" in client.get("/account").text
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 3")
    detail = client.get(detail_url).text
    assert "Ready" in detail
    assert "Mark Sent" in detail
    assert "We couldn't deliver your last piece of mail" in client.get("/account").text

    mark_sole_draft_sent(client, csrf, detail_url)
    account = client.get("/account").text
    assert "We couldn't deliver your last piece of mail" not in account


def test_mark_sent_records_the_operator_local_date(client, mailer, monkeypatch):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    recorded_at = datetime(2026, 8, 4, 3, 30)
    monkeypatch.setattr(admin, "utcnow", lambda: recorded_at)

    detail = client.get(detail_url).text
    assert 'name="sent_on"' not in detail
    response = mark_sole_draft_sent(client, csrf, detail_url)
    assert response.status_code == 200

    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.sent_at == recorded_at
        assert mailpiece.sent_on == date(2026, 8, 3)
    assert "Sent 2026-08-03" in response.text
    raw_table = client.get("/admin?table=mailpieces").text
    assert "2026-08-04 03:30:00" in raw_table


def test_mailpiece_pins_current_address(client, mailer):
    sign_up_and_verify(client, mailer)
    update_account(client, OCKHAM_PARK)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    mark_sole_draft_sent(client, csrf, detail_url)
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
    mark_sole_draft_sent(client, csrf, detail_url)

    sent = client.get(detail_url).text
    assert "12 Analytical Way<br>Apartment 2B<br>" in sent
    assert "London, Greater London N1 9GU<br>" in sent


def test_ready_draft_offers_normalized_address_export(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)

    detail = client.get(create_edition(client, csrf)).text
    assert "<th>Address</th>" in detail
    assert 'href="/admin/editions/' in detail
    assert '/labels.csv">Export Ready Addresses</a>' in detail


def test_unsubscribed_excluded_from_edition_list(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "No mailpieces yet." in detail
    assert "Ada Lovelace" not in detail
    assert "Needs review" not in detail


def test_signup_after_the_edition_is_left_off_it(client, mailer):
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)
    normalize_current_address(client, csrf)
    user_id = sole_user_id(client)

    detail = client.get(detail_url).text
    assert "No mailpieces yet." in detail
    assert "Needs review" not in detail
    assert "NEW: Ada Lovelace &lt;ada@example.com&gt;" in detail

    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" not in labels.text

    response = client.post(
        f"{detail_url}/drafts",
        data={"csrf": csrf, "user_id": user_id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    detail = client.get(detail_url).text
    assert "Ready" in detail
    assert "NEW: Ada Lovelace" not in detail
    assert "Ada Lovelace" in client.get(f"{detail_url}/labels.csv").text

    later = create_edition(client, csrf)
    assert "Ready" in client.get(later).text


def test_mark_sent_rejects_unsubscribed_user(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    user_id = sole_user_id(client)
    detail_url = create_edition(client, csrf)
    normalize_current_address(client, csrf)
    draft_id = sole_draft_id(client)
    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    response = client.post(
        f"{detail_url}/drafts/{draft_id}/mark-sent",
        data={"csrf": csrf},
    )
    assert response.status_code == 409
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_later_signup_can_be_added_exported_and_marked_sent(client, mailer):
    csrf = admin_login(client)
    edition = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)
    normalize_current_address(client, csrf)
    user_id = sole_user_id(client)

    response = client.post(
        f"{edition}/drafts",
        data={"csrf": csrf, "user_id": user_id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    label = client.get(f"{edition}/labels.csv")
    assert label.status_code == 200
    assert label.headers["cache-control"] == "no-store"
    assert (
        f"edition-{edition.rsplit('/', maxsplit=1)[-1]}-to-send.csv"
        in (label.headers["content-disposition"])
    )
    assert "Ada Lovelace" in label.text

    response = mark_sole_draft_sent(client, csrf, edition, follow_redirects=False)
    assert response.status_code == 303
    detail = client.get(edition).text
    assert "Sent " in detail
    assert "Mark Returned" in detail
    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(Mailpiece)).all()) == 1
        assert db.scalars(select(MailpieceDraft)).all() == []


def test_added_ready_drafts_export_together(client, mailer):
    csrf = admin_login(client)
    edition = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)
    normalize_current_address(client, csrf)

    user_csrf = csrf_from(client.get("/account").text)
    client.post("/logout", data={"csrf": user_csrf})
    signup_csrf = csrf_from(client.get("/").text)
    grace = {**SIGNUP_FORM, "email": "grace@example.com", "name": "Grace Hopper"}
    client.post("/signup", data={**grace, "csrf": signup_csrf})
    client.post(
        "/signup/verify",
        data={**grace, "csrf": signup_csrf, "code": mailer.last_code()},
        follow_redirects=False,
    )
    normalize_current_address(client, csrf)

    with Session(client.app.state.engine) as db:
        user_ids = dict(db.execute(select(User.email, User.id)).all())

    for user_id in user_ids.values():
        response = client.post(
            f"{edition}/drafts",
            data={"csrf": csrf, "user_id": user_id},
            follow_redirects=False,
        )
        assert response.status_code == 303

    detail = client.get(edition).text
    assert len(re.findall(r"<td>\s*Ready\s*</td>", detail)) == 2
    labels = client.get(f"{edition}/labels.csv")
    assert labels.status_code == 200
    assert "Ada Lovelace" in labels.text
    assert "Grace Hopper" in labels.text


def test_added_draft_needing_review_uses_the_existing_address_workflow(client, mailer):
    csrf = admin_login(client)
    edition = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)
    user_id = sole_user_id(client)

    detail = client.get(edition).text
    assert "NEW: Ada Lovelace" in detail
    assert "No mailpieces yet." in detail
    client.post(
        f"{edition}/drafts",
        data={"csrf": csrf, "user_id": user_id},
    )
    detail = client.get(edition).text
    assert "Needs review" in detail
    assert "Normalize" in detail
    labels = client.get(f"{edition}/labels.csv")
    assert labels.status_code == 200
    assert labels.text.splitlines() == [
        ",".join(field.name for field in fields(AddressComponents))
    ]

    normalize_current_address(client, csrf)
    detail = client.get(edition).text
    assert "Ready" in detail
    assert "<th>Address</th>" in detail
    assert "Normalize" in detail
    assert "Export Ready Addresses" in detail
    assert "<th>Status</th>" in detail


def test_draft_candidates_follow_the_live_subscription(client, mailer):
    csrf = admin_login(client)
    edition = create_edition(client, csrf)
    sign_up_and_verify(client, mailer)
    user_id = sole_user_id(client)
    assert "NEW: Ada Lovelace" in client.get(edition).text

    client.post(f"/admin/users/{user_id}/unsubscribe", data={"csrf": csrf})
    assert "Ada Lovelace" not in client.get(edition).text

    client.post(f"/admin/users/{user_id}/resubscribe", data={"csrf": csrf})
    assert "NEW: Ada Lovelace" in client.get(edition).text


def test_mark_sent_with_an_unknown_draft_is_404(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    response = client.post(
        f"{detail_url}/drafts/nope/mark-sent",
        data={"csrf": csrf},
    )
    assert response.status_code == 404
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []


def test_mark_sent_rechecks_the_current_normalized_address(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    draft_id = sole_draft_id(client)
    assert "Mark Sent" in client.get(detail_url).text
    update_account(client, OCKHAM_PARK)
    response = client.post(
        f"{detail_url}/drafts/{draft_id}/mark-sent",
        data={"csrf": csrf},
    )
    assert response.status_code == 409
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Mailpiece)).all() == []
        assert len(db.scalars(select(MailpieceDraft)).all()) == 1


def test_normalized_address_used_for_mailing_but_not_account_page(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 3")

    assert "Flat 3" not in client.get("/account").text

    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "Flat 3" in detail
    assert "Flat 3" in client.get(f"{detail_url}/labels.csv").text

    mark_sole_draft_sent(client, csrf, detail_url)
    with Session(client.app.state.engine) as db:
        mailpiece = db.scalars(select(Mailpiece)).one()
        assert mailpiece.normalized_address.address_line1 == "12 Analytical Way, Flat 3"
        assert mailpiece.normalized_address.address.address_line1 == "12 Analytical Way"
    assert "12 Analytical Way, Flat 3" in client.get(detail_url).text


def test_normalize_files_the_shown_address_as_the_print_version(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "Needs review" in detail

    normalize_url = re.search(
        r'href="(/admin/addresses/[^"]+/normalize\?edition=[^"]+)"', detail
    ).group(1)
    normalize_page = client.get(normalize_url).text
    assert "The user entered:" in normalize_page
    assert "Save Normalized Address" in normalize_page
    normalize_current_address(client, csrf)

    with Session(client.app.state.engine) as db:
        normalized_address = db.scalars(select(NormalizedAddress)).one()
        assert normalized_address.addressee == "Ada Lovelace"
        assert normalized_address.address_line1 == "12 Analytical Way"
        assert normalized_address.postal_code == "N1 9GU"
    detail = client.get(detail_url).text
    assert "Needs review" not in detail
    assert "Ready" in detail


def test_database_rejects_another_users_normalized_address(client, mailer):
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
    with Session(client.app.state.engine) as db:
        db.add(
            Mailpiece(
                edition_id=detail_url.rsplit("/", 1)[-1],
                user_id=ada_id,
                normalized_address_id=graces_normalized_address,
                sent_on=date.today(),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_new_address_supersedes_the_old_rows_normalized_address(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf, address_line1="12 Analytical Way, Flat 3")
    update_account(client, OCKHAM_PARK)

    detail_url = create_edition(client, csrf)
    detail = client.get(detail_url).text
    assert "Needs review" in detail
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
    component_fields = {field.name for field in fields(AddressComponents)}
    for table in (Address, NormalizedAddress):
        columns = {column.name: column for column in table.__table__.columns}
        stored = set(columns) - {"id", "user_id", "address_id", "created_at"}
        assert component_fields == stored
        assert optional == {name for name in stored if columns[name].nullable}


def test_saving_the_normalize_form_untouched_appends_no_print_version(client, mailer):
    """Normalize files the address as entered, then prefills that print version."""
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
    mark_sole_draft_sent(client, csrf, detail_url)
    labels = client.get(f"{detail_url}/labels.csv")
    assert "Ada Lovelace" not in labels.text


def test_csv_export_matches_table(client, mailer):
    sign_up_and_verify(client, mailer)
    admin_login(client)
    response = client.get("/admin/export.csv?table=users")
    assert response.status_code == 200
    header = response.text.splitlines()[0]
    assert header.startswith("id,email,unsubscribed_at")
    assert header.endswith("current_address,current_normalized_address")
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


def test_multiple_drafts_create_multiple_mailpieces_for_one_user(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = admin_login(client)
    normalize_current_address(client, csrf)
    detail_url = create_edition(client, csrf)
    user_id = sole_user_id(client)
    mark_sole_draft_sent(client, csrf, detail_url)
    response = client.post(
        f"{detail_url}/drafts",
        data={"csrf": csrf, "user_id": user_id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    mark_sole_draft_sent(client, csrf, detail_url)

    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(Mailpiece)).all()) == 2
        assert db.scalars(select(MailpieceDraft)).all() == []
