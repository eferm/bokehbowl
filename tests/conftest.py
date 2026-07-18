import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from bokehbowl.app import create_app
from bokehbowl.config import AppConfig, ConsoleMail
from bokehbowl.db import Base

ADMIN_PASSWORD = "test-admin-password"


class CaptureMailer:
    def __init__(self):
        self.sent = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append((to, subject, body))

    def last_code(self) -> str:
        _, _, body = self.sent[-1]
        return re.search(r"\b(\d{6})\b", body).group(1)


@pytest.fixture()
def mailer():
    return CaptureMailer()


@pytest.fixture()
def client(mailer):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app(
        config=AppConfig(
            database_url="sqlite://",
            session_secret="test-secret",
            admin_password=ADMIN_PASSWORD,
            cookie_secure=True,
            mail=ConsoleMail(),
            operator_name="Testy Operator",
            operator_contact="operator@example.com",
            commit="abc1234def5678",
        ),
        engine=engine,
        mailer=mailer,
    )
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def csrf_from(page_html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', page_html).group(1)


SIGNUP_FORM = {
    "name": "Ada Lovelace",
    "email": "Ada@Example.com",
    "address_line1": "12 Analytical Way",
    "address_line2": "",
    "city": "London",
    "region": "",
    "postal_code": "N1 9GU",
    "country": "United Kingdom",
}


def sign_up_and_verify(client, mailer) -> None:
    csrf = csrf_from(client.get("/").text)
    response = client.post(
        "/signup", data={**SIGNUP_FORM, "csrf": csrf}, follow_redirects=True
    )
    assert response.status_code == 200
    response = client.post(
        "/verify",
        data={"csrf": csrf, "email": "ada@example.com", "code": mailer.last_code()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/account?created=1"
