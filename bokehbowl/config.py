"""Application configuration, parsed from the environment exactly once at startup."""

import os
from dataclasses import dataclass
from pathlib import Path


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
    commit: str | None


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


def read_git_commit() -> str | None:
    """The checked-out commit, read from .git files without the git binary."""
    try:
        head = Path(".git/HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head
        ref = head.removeprefix("ref: ")
        ref_file = Path(".git") / ref
        if ref_file.is_file():
            return ref_file.read_text().strip()
        for line in Path(".git/packed-refs").read_text().splitlines():
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha
    except OSError:
        return None
    return None


def load_config() -> AppConfig:
    return AppConfig(
        database_url=os.environ.get("DATABASE_URL", "sqlite:///data/bokehbowl.db"),
        session_secret=os.environ["SESSION_SECRET"],
        admin_password=os.environ["ADMIN_PASSWORD"],
        cookie_secure=os.environ.get("COOKIE_SECURE", "true") == "true",
        mail=MAIL_BACKENDS[os.environ.get("MAIL_BACKEND", "console")](),
        operator_name=os.environ.get("OPERATOR_NAME", "the operator of this instance"),
        operator_contact=os.environ.get("OPERATOR_CONTACT", ""),
        commit=os.environ.get("GIT_COMMIT") or read_git_commit(),
    )
