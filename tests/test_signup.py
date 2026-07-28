import base64
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from bokehbowl import auth, web
from bokehbowl.config import load_config
from bokehbowl.db import Address, LoginCode, User, UserSession, utcnow
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
        "/signup/verify",
        data={**SIGNUP_FORM, "csrf": csrf, "code": mailer.last_code()},
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
    assert "user_id" not in payload
    assert isinstance(payload["user_token"], str)
    assert len(payload["user_token"]) == 43


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
        "/signup/verify", data={**SIGNUP_FORM, "csrf": csrf, "code": wrong}
    )
    assert response.status_code == 422
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_account_fields_locked_until_edit(client, mailer):
    sign_up_and_verify(client, mailer)
    locked = client.get("/account").text
    assert 'class="form-grid" disabled' in locked
    assert 'href="/account?edit=1"' in locked
    editing = client.get("/account?edit=1").text
    assert 'class="form-grid" disabled' not in editing
    assert "Save" in editing


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


def test_unsubscribe_and_resubscribe(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    response = client.post(
        "/account/unsubscribe", data={"csrf": csrf}, follow_redirects=True
    )
    assert "unsubscribed" in response.text

    client.post("/login", data={"csrf": csrf, "email": "ada@example.com"})
    client.post(
        "/login/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
    )
    account = client.get("/account")
    assert "Resubscribe" in account.text
    response = client.post(
        "/account/resubscribe", data={"csrf": csrf}, follow_redirects=True
    )
    assert "Resubscribe" not in response.text


def test_cookie_replay_rejected_after_logout(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    saved = dict(client.cookies)
    logout = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
    assert logout.status_code == 303
    client.cookies = saved
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_cookie_replay_rejected_after_unsubscribe(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)
    saved = dict(client.cookies)
    unsubscribe = client.post(
        "/account/unsubscribe", data={"csrf": csrf}, follow_redirects=False
    )
    assert unsubscribe.status_code == 303
    client.cookies = saved
    assert client.get("/account", follow_redirects=False).status_code == 303


def test_logout_only_ends_current_device_session(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/account").text)

    with Session(client.app.state.engine) as db:
        current_session = db.scalars(select(UserSession)).one()
        db.add(
            UserSession(
                user_id=current_session.user_id,
                token="another-device-session",
            )
        )
        db.commit()

    response = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303

    with Session(client.app.state.engine) as db:
        sessions = db.scalars(select(UserSession)).all()
        assert [session.token for session in sessions] == ["another-device-session"]


def test_verification_prunes_expired_user_sessions(client, mailer):
    sign_up_and_verify(client, mailer)
    with Session(client.app.state.engine) as db:
        session = db.scalars(select(UserSession)).one()
        session.created_at = utcnow() - web.USER_SESSION_TTL - timedelta(seconds=1)
        db.commit()

    csrf = csrf_from(client.get("/").text)
    client.post("/login", data={"csrf": csrf, "email": "ada@example.com"})
    client.post(
        "/login/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
    )

    with Session(client.app.state.engine) as db:
        assert len(db.scalars(select(UserSession)).all()) == 1


def test_signup_state_survives_mailer_failure(client, mailer, monkeypatch):
    def boom(to, subject, body):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer, "send", boom)
    csrf = csrf_from(client.get("/").text)
    with pytest.raises(RuntimeError):
        client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(LoginCode)).one()
        assert db.scalars(select(User)).all() == []
        assert db.scalars(select(Address)).all() == []


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
        "/login/verify",
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


def test_repeat_signup_verifies_with_the_latest_payload(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    revised = {
        **SIGNUP_FORM,
        "name": "Grace Hopper",
        "address_line1": "1 Navy Yard",
        "city": "Arlington",
    }
    client.post("/signup", data={**revised, "csrf": csrf})
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(User)).all() == []
        assert db.scalars(select(Address)).all() == []
    client.post(
        "/signup/verify",
        data={**revised, "csrf": csrf, "code": mailer.last_code()},
    )
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(User)).one()
        address = db.scalars(select(Address)).one()
        assert address.addressee == "Grace Hopper"
        assert address.address_line1 == "1 Navy Yard"
        assert address.city == "Arlington"


def test_existing_user_signup_signs_in_and_keeps_the_saved_address(client, mailer):
    sign_up_and_verify(client, mailer)
    csrf = csrf_from(client.get("/").text)
    revised = {
        **SIGNUP_FORM,
        "name": "Someone Else",
        "address_line1": "99 Other Road",
    }
    client.post("/signup", data={**revised, "csrf": csrf})
    response = client.post(
        "/signup/verify",
        data={**revised, "csrf": csrf, "code": mailer.last_code()},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/account?existing=1"

    account = client.get("/account?existing=1")
    assert "You already had an account" in account.text
    assert "12 Analytical Way" in account.text
    assert "99 Other Road" not in account.text

    with Session(client.app.state.engine) as db:
        assert db.scalars(select(User)).one()
        address = db.scalars(select(Address)).one()
        assert address.addressee == "Ada Lovelace"


def request_codes(app, address: str, count: int, prefix: str = "user") -> list[int]:
    """Submit `count` signups from one client address, a fresh email each."""
    with TestClient(app, base_url="https://testserver", client=(address, 999)) as guest:
        csrf = csrf_from(guest.get("/").text)
        return [
            guest.post(
                "/signup",
                data={**SIGNUP_FORM, "email": f"{prefix}{n}@example.com", "csrf": csrf},
            ).status_code
            for n in range(count)
        ]


def test_code_requests_are_capped_per_address(client, mailer):
    cap = client.app.state.code_request_throttle.cap
    statuses = request_codes(client.app, "10.0.0.1", cap + 1)
    assert statuses == [200] * cap + [429]
    assert len(mailer.sent) == cap


def test_resend_inside_the_cooldown_leaves_the_code_budget_alone(client, mailer):
    """The budget counts codes, so mashing Resend during the cooldown spends
    none of it."""
    csrf = csrf_from(client.get("/").text)
    cap = client.app.state.code_request_throttle.cap
    statuses = [
        client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf}).status_code
        for _ in range(cap + 3)
    ]

    assert statuses == [200] * (cap + 3)
    assert len(mailer.sent) == 1


def test_a_throttled_signup_keeps_the_address_on_the_page(client, mailer):
    cap = client.app.state.code_request_throttle.cap
    request_codes(client.app, "10.0.0.5", cap, prefix="flood")
    with TestClient(
        client.app, base_url="https://testserver", client=("10.0.0.5", 999)
    ) as guest:
        csrf = csrf_from(guest.get("/").text)
        response = guest.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})

    assert response.status_code == 429
    assert "Too many code requests from here" in response.text
    assert "12 Analytical Way" in response.text
    assert "on its way" not in response.text


def test_login_budget_spends_the_same_on_an_email_without_an_account(client, mailer):
    """Codes for unknown emails cost what codes for known ones cost, so the cap
    keeps quiet about who has an account."""
    cap = client.app.state.code_request_throttle.cap
    with TestClient(
        client.app, base_url="https://testserver", client=("10.0.0.6", 999)
    ) as guest:
        csrf = csrf_from(guest.get("/").text)
        statuses = [
            guest.post(
                "/login",
                data={"csrf": csrf, "email": f"nobody{n}@example.com"},
                follow_redirects=False,
            ).status_code
            for n in range(cap + 1)
        ]

    assert statuses == [303] * cap + [429]
    assert mailer.sent == []


def test_signup_verify_page_reached_directly_starts_at_the_signup_form(client):
    response = client.get("/signup/verify", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_one_address_cannot_exhaust_the_code_budget(client, mailer):
    cap = client.app.state.code_request_throttle.cap
    request_codes(client.app, "10.0.0.1", cap, prefix="flood")
    assert request_codes(client.app, "10.0.0.2", 1, prefix="grace") == [200]


def burn_attempts(app, address: str, code: str, count: int) -> None:
    """Submit a wrong code `count` times from one client address."""
    with TestClient(app, base_url="https://testserver", client=(address, 999)) as guest:
        csrf = csrf_from(guest.get("/").text)
        for _ in range(count):
            response = guest.post(
                "/signup/verify", data={**SIGNUP_FORM, "csrf": csrf, "code": code}
            )
            assert response.status_code == 422


def test_attempt_cap_blocks_correct_code(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    correct = mailer.last_code()
    wrong = "000000" if correct != "000000" else "111111"

    # Spread across addresses so the per-address throttle never fires and the
    # code's own attempt cap is what runs out.
    cap = client.app.state.code_attempt_throttle.cap
    burn_attempts(client.app, "10.0.0.1", wrong, cap)
    burn_attempts(client.app, "10.0.0.2", wrong, auth.MAX_ATTEMPTS - cap)

    response = client.post(
        "/signup/verify", data={**SIGNUP_FORM, "csrf": csrf, "code": correct}
    )
    assert response.status_code == 422


def test_one_address_cannot_burn_a_code_to_death(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    correct = mailer.last_code()
    wrong = "000000" if correct != "000000" else "111111"

    burn_attempts(
        client.app, "10.0.0.1", wrong, client.app.state.code_attempt_throttle.cap
    )
    with TestClient(
        client.app, base_url="https://testserver", client=("10.0.0.1", 999)
    ) as attacker:
        attacker_csrf = csrf_from(attacker.get("/").text)
        response = attacker.post(
            "/signup/verify",
            data={**SIGNUP_FORM, "csrf": attacker_csrf, "code": wrong},
        )
        assert response.status_code == 429
        assert "Too many attempts" in response.text

    # The code survives the burn, so its owner still signs in.
    response = client.post(
        "/signup/verify",
        data={**SIGNUP_FORM, "csrf": csrf, "code": correct},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_consumed_code_cannot_be_replayed(client, mailer):
    sign_up_and_verify(client, mailer)
    code = mailer.last_code()
    csrf = csrf_from(client.get("/account").text)
    response = client.post(
        "/signup/verify", data={**SIGNUP_FORM, "csrf": csrf, "code": code}
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
        assert db.scalars(select(User)).all() == []
        assert db.scalars(select(Address)).all() == []
