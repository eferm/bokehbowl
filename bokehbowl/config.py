"""Application configuration, parsed from the environment exactly once at startup."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ConsoleMail:
    """Print login codes to stdout. For local development and tests."""


@dataclass(frozen=True)
class SmtpMail:
    """Send via authenticated SMTP (Cloudflare Email Service, or any provider)."""

    host: str
    port: int
    username: str
    password: str
    sender: str


MailConfig = ConsoleMail | SmtpMail


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    session_secret: str
    admin_password: str
    cookie_secure: bool
    mail: MailConfig
    operator_name: str
    operator_contact: str


def load_smtp_mail() -> SmtpMail:
    return SmtpMail(
        host=os.environ["SMTP_HOST"],
        port=int(os.environ["SMTP_PORT"]),
        username=os.environ["SMTP_USERNAME"],
        password=os.environ["SMTP_PASSWORD"],
        sender=os.environ["MAIL_SENDER"],
    )


MAIL_BACKENDS = {
    "console": lambda: ConsoleMail(),
    "smtp": load_smtp_mail,
}


def load_config() -> AppConfig:
    return AppConfig(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///data/bokehbowl.db"),
        session_secret=os.environ["SESSION_SECRET"],
        admin_password=os.environ["ADMIN_PASSWORD"],
        cookie_secure=os.environ.get("COOKIE_SECURE", "true") == "true",
        mail=MAIL_BACKENDS[os.environ.get("MAIL_BACKEND", "console")](),
        operator_name=os.environ.get("OPERATOR_NAME", "the operator of this instance"),
        operator_contact=os.environ.get("OPERATOR_CONTACT", ""),
    )
