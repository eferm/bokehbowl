import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from bokehbowl import auth, web
from bokehbowl.config import load_config
from bokehbowl.db import LoginCode, Recipient, RecipientSession, utcnow
from tests.conftest import SIGNUP_FORM, csrf_from, sign_up_and_verify


def test_signup_sends_code_and_verify_logs_in(client, mailer):
    sign_up_and_verify(client, mailer)
    account = client.get("/account")
    assert account.status_code == 200
    assert "ada@example.com" in account.text
    assert "Ada Lovelace" in account.text
    assert "Full Name" in account.text
    assert "State / Province" in account.text
    assert "Postal Code" in account.text


def test_first_signup_shows_confirmation(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    response = client.post(
        "/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
        follow_redirects=True,
    )
    assert "You're on the list." in response.text


def test_authenticated_header_offers_sign_out(client, mailer):
    sign_up_and_verify(client, mailer)
    assert "Sign out" in client.get("/").text


def test_session_cookie_carries_token(client, mailer):
    """The client-readable session payload contains only an opaque token."""
    sign_up_and_verify(client, mailer)
    encoded = client.cookies["session"].split(".")[0]
    payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert "recipient_id" not in payload
    assert isinstance(payload["recipient_token"], str)
    assert len(payload["recipient_token"]) == 43


def test_verified_signup_notifies_operator(client, mailer):
    sign_up_and_verify(client, mailer)
    to, subject, body = mailer.sent[-1]
    assert to == "notify@example.com"
    assert subject == "New signup: Ada Lovelace"
    assert "Ada Lovelace <ada@example.com>" in body


def test_notify_email_falls_back_to_operator_email(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "password")
    monkeypatch.setenv("OPERATOR_EMAIL", "operator@example.com")
    monkeypatch.delenv("NOTIFY_EMAIL", raising=False)
    assert load_config().notify_email == "operator@example.com"


def test_email_is_normalized_and_not_duplicated(client, mailer):
    sign_up_and_verify(client, mailer)
    to, _, _ = mailer.sent[0]
    assert to == "ada@example.com"


def test_wrong_code_rejected(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    wrong = "000000" if mailer.last_code() != "000000" else "111111"
    response = client.post(
        "/verify", data={"csrf": csrf, "email": "ada@example.com", "code": wrong}
    )
    assert response.status_code == 422
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_account_update(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    response = client.post(
        "/account",
        data={
            "csrf": csrf,
            "name": "Ada King",
            "address_line1": "1 Ockham Park",
            "address_line2": "",
            "city": "Surrey",
            "region": "",
            "postal_code": "GU23 6NQ",
            "country": "United Kingdom",
        },
        follow_redirects=True,
    )
    assert "Ada King" in response.text
    assert "Saved." in response.text


def test_unregister_and_rejoin(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    response = client.post(
        "/account/unregister", data={"csrf": csrf}, follow_redirects=True
    )
    assert "unregistered" in response.text

    client.post("/login", data={"csrf": csrf, "email": "ada@example.com"})
    client.post(
        "/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
    )
    account = client.get("/account")
    assert "Reregister" in account.text
    response = client.post(
        "/account/reregister", data={"csrf": csrf}, follow_redirects=True
    )
    assert "Reregister" not in response.text


def test_cookie_replay_rejected_after_logout(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    saved = dict(client.cookies)
    logout = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
    assert logout.status_code == 303
    client.cookies = saved
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_cookie_replay_rejected_after_unregister(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    saved = dict(client.cookies)
    unregister = client.post(
        "/account/unregister", data={"csrf": csrf}, follow_redirects=False
    )
    assert unregister.status_code == 303
    client.cookies = saved
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_logout_only_ends_current_device_session(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)

    with Session(client.app.state.engine) as db:
        current_session = db.scalars(select(RecipientSession)).one()
        db.add(
            RecipientSession(
                recipient_id=current_session.recipient_id,
                token="another-device-session",
            )
        )
        db.commit()

    response = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303

    with Session(client.app.state.engine) as db:
        sessions = db.scalars(select(RecipientSession)).all()
        assert [session.token for session in sessions] == ["another-device-session"]


def test_verification_prunes_expired_recipient_sessions(client, mailer):
    sign_up_and_verify(client, mailer)
    with Session(client.app.state.engine) as db:
        session = db.scalars(select(RecipientSession)).one()
        session.created_at = utcnow() - web.RECIPIENT_SESSION_TTL - timedelta(
            seconds=1
        )
        db.commit()

    csrf = csrf_from(client.get("/").text)
    client.post("/login", data={"csrf": csrf, "email": "ada@example.com"})
    client.post(
        "/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
    )

    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(RecipientSession)).all()) == 1


def test_signup_state_survives_mailer_failure(client, mailer, monkeypatch):
    def boom(to, subject, body):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    csrf = csrf_from(client.get("/").text)
    with pytest.raises(RuntimeError):
        client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(Recipient)).one()
        assert db.scalars(select(LoginCode)).one()


def test_signup_rejects_address_lists(client, mailer):
    csrf = csrf_from(client.get("/").text)
    bad = ["a@example.com,b@example.com", "not-an-address"]
    for email in bad:
        response = client.post(
            "/signup", data={**SIGNUP_FORM, "email": email, "csrf": csrf}
        )
        assert response.status_code == 422
    assert mailer.sent == []


def test_stale_cookie_cannot_log_out_new_session(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    stale = dict(client.cookies)
    client.post("/logout", data={"csrf": csrf})
    client.post("/login", data={"csrf": csrf, "email": "ada@example.com"})
    client.post(
        "/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
    )
    fresh = dict(client.cookies)

    client.cookies = stale
    replay = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
    assert replay.headers["location"] == "/"
    assert "Sign out" not in client.get("/").text
    client.cookies = fresh
    assert client.get("/account").status_code == 200


def test_oversized_field_rejected(client, mailer):
    csrf = csrf_from(client.get("/").text)
    response = client.post(
        "/signup", data={**SIGNUP_FORM, "name": "A" * 10_000, "csrf": csrf}
    )
    assert response.status_code == 422
    assert mailer.sent == []


def test_csrf_required_on_signup(client):
    response = client.post("/signup", data={**SIGNUP_FORM, "csrf": "forged"})
    assert response.status_code == 403


def test_resend_is_rate_limited(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    client.post("/login", data={"csrf": csrf, "email": "ada@example.com"})
    assert len(mailer.sent) == 1


def test_code_volume_is_capped(client, mailer, monkeypatch):
    monkeypatch.setattr(auth, "HOURLY_CODE_CAP", 2)
    csrf = csrf_from(client.get("/").text)
    responses = [
        client.post(
            "/signup",
            data={**SIGNUP_FORM, "email": f"user{n}@example.com", "csrf": csrf},
            follow_redirects=False,
        )
        for n in range(3)
    ]
    assert responses[2].status_code == 429
    assert len(mailer.sent) == 2


def test_unverified_signup_data_is_overwritten(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    client.post(
        "/signup",
        data={
            **SIGNUP_FORM,
            "csrf": csrf,
            "name": "Grace Hopper",
            "address_line1": "1 Navy Yard",
            "city": "Arlington",
        },
    )
    with Session(client.app.state.engine) as db:
        recipient = db.scalars(select(Recipient)).one()
        assert recipient.name == "Grace Hopper"
        assert recipient.address_line1 == "1 Navy Yard"
        assert recipient.city == "Arlington"
    client.post(
        "/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
    )
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf, "name": "Someone Else"})
    with Session(client.app.state.engine) as db:
        recipient = db.scalars(select(Recipient)).one()
        assert recipient.name == "Grace Hopper"


def test_attempt_cap_blocks_correct_code(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    correct = mailer.last_code()
    wrong = "000000" if correct != "000000" else "111111"
    for _ in range(5):
        response = client.post(
            "/verify", data={"csrf": csrf, "email": "ada@example.com", "code": wrong}
        )
        assert response.status_code == 422
    response = client.post(
        "/verify", data={"csrf": csrf, "email": "ada@example.com", "code": correct}
    )
    assert response.status_code == 422


def test_consumed_code_cannot_be_replayed(client, mailer):
    sign_up_and_verify(client, mailer)
    code = mailer.last_code()
    csrf = csrf_from(client.get("/account").text)
    response = client.post(
        "/verify", data={"csrf": csrf, "email": "ada@example.com", "code": code}
    )
    assert response.status_code == 422


def test_capped_signup_creates_no_row(client, mailer, monkeypatch):
    monkeypatch.setattr(auth, "HOURLY_CODE_CAP", 1)
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    response = client.post(
        "/signup",
        data={**SIGNUP_FORM, "email": "grace@example.com", "csrf": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 429
    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(Recipient)).all()) == 1
