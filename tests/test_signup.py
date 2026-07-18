import base64
import json

from tests.conftest import SIGNUP_FORM, csrf_from, sign_up_and_verify


def test_signup_sends_code_and_verify_logs_in(client, mailer):
    sign_up_and_verify(client, mailer)
    account = client.get("/account")
    assert account.status_code == 200
    assert "ada@example.com" in account.text
    assert "Ada Lovelace" in account.text


def test_session_cookie_carries_uuid(client, mailer):
    """The session cookie payload is client-readable base64; the recipient id
    inside is a UUID string."""
    sign_up_and_verify(client, mailer)
    encoded = client.cookies["session"].split(".")[0]
    payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert isinstance(payload["recipient_id"], str)
    assert len(payload["recipient_id"]) == 36


def test_email_is_normalized_and_not_duplicated(client, mailer):
    sign_up_and_verify(client, mailer)
    to, _, _ = mailer.sent[-1]
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


def test_csrf_required_on_signup(client):
    response = client.post("/signup", data={**SIGNUP_FORM, "csrf": "forged"})
    assert response.status_code == 403


def test_resend_is_rate_limited(client, mailer):
    csrf = csrf_from(client.get("/").text)
    client.post("/signup", data={**SIGNUP_FORM, "csrf": csrf})
    client.post("/login", data={"csrf": csrf, "email": "ada@example.com"})
    assert len(mailer.sent) == 1
