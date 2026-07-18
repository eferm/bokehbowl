"""Login codes (email OTP), CSRF tokens, and session access."""

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from bokehbowl.db import LoginCode, utcnow
from bokehbowl.mailer import Mailer

CODE_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5


class CooldownActive(Exception):
    """A code was issued for this email too recently."""


def hash_code(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def latest_code(db: Session, email: str) -> LoginCode | None:
    return db.scalar(
        select(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    )


def issue_login_code(db: Session, email: str, now: datetime) -> str:
    """Create and store a fresh code. Raises CooldownActive when issued too recently."""
    current = latest_code(db, email)
    if current is not None and now - current.created_at < RESEND_COOLDOWN:
        raise CooldownActive(email)
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        LoginCode(
            email=email,
            code_hash=hash_code(email, code),
            created_at=now,
            expires_at=now + CODE_TTL,
        )
    )
    return code


def consume_login_code(db: Session, email: str, code: str, now: datetime) -> bool:
    """Check a submitted code, burning one attempt. Consumes the code on success."""
    current = latest_code(db, email)
    if current is None or now > current.expires_at or current.attempts >= MAX_ATTEMPTS:
        return False
    current.attempts += 1
    if not secrets.compare_digest(current.code_hash, hash_code(email, code)):
        return False
    current.consumed_at = now
    return True


def csrf_token(request: Request) -> str:
    token = request.session.setdefault("csrf", secrets.token_urlsafe(16))
    return token


def require_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf")
    if expected is None or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def send_login_code(db: Session, mailer: Mailer, email: str) -> None:
    """Issue a code and email it; silently keeps the current code during cooldown."""
    try:
        code = issue_login_code(db, email, utcnow())
    except CooldownActive:
        return
    mailer.send(
        to=email,
        subject=f"{code} is your bokehbowl code",
        body=(
            f"Your bokehbowl sign-in code is: {code}\n\n"
            f"It expires in 10 minutes. If you didn't request this, ignore this email."
        ),
    )
