"""Outbound email port and its adapters."""

import smtplib
from email.message import EmailMessage
from typing import Protocol

from bokehbowl.config import ConsoleMail, MailConfig, SmtpMail


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    """Prints mail to stdout so `git clone` → running needs no email provider."""

    def send(self, to: str, subject: str, body: str) -> None:
        print(f"--- mail to {to} ---\n{subject}\n\n{body}\n---")


class SmtpMailer:
    """Authenticated SMTP submission. Works with Cloudflare Email Service
    (smtp.mx.cloudflare.net:465) or any other provider."""

    def __init__(self, config: SmtpMail):
        self.config = config

    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.config.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP_SSL(self.config.host, self.config.port) as smtp:
            smtp.login(self.config.username, self.config.password)
            smtp.send_message(message)


def build_mailer(config: MailConfig) -> Mailer:
    match config:
        case ConsoleMail():
            return ConsoleMailer()
        case SmtpMail():
            return SmtpMailer(config)
