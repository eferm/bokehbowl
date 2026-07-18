"""Login codes (email OTP), CSRF tokens, and session access."""

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from bokehbowl.db import LoginCode, utcnow
from bokehbowl.mailer import Mailer

CODE_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5
HOURLY_CODE_CAP = 50
DAILY_CODE_CAP = 300


def hash_code(email: str, code: str) -> str:
    return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()


def latest_code(db: Session, email: str) -> LoginCode | None:
    return db.scalar(
        select(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    )


def codes_issued_since(db: Session, cutoff: datetime) -> int:
    return db.scalar(
        select(func.count())
        .select_from(LoginCode)
        .where(LoginCode.created_at >= cutoff)
    )


def volume_capped(db: Session, now: datetime) -> bool:
    """True when the instance-wide hourly or daily code volume cap is reached."""
    if codes_issued_since(db, now - timedelta(hours=1)) >= HOURLY_CODE_CAP:
        return True
    return codes_issued_since(db, now - timedelta(days=1)) >= DAILY_CODE_CAP


def cooldown_active(db: Session, email: str, now: datetime) -> bool:
    """True when the latest unconsumed code for the email is younger than
    RESEND_COOLDOWN."""
    current = latest_code(db, email)
    return current is not None and now - current.created_at < RESEND_COOLDOWN


def issue_login_code(db: Session, email: str, now: datetime) -> str:
    """Create and store a fresh code, returning it."""
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
    """Check a submitted code, atomically burning one attempt. Consumes the code
    on success; returns True on a match within the attempt cap and TTL."""
    current = latest_code(db, email)
    if current is None or now > current.expires_at:
        return False
    claimed = db.execute(
        update(LoginCode)
        .where(
            LoginCode.id == current.id,
            LoginCode.consumed_at.is_(None),
            LoginCode.attempts < MAX_ATTEMPTS,
        )
        .values(attempts=LoginCode.attempts + 1)
    )
    if claimed.rowcount != 1:
        return False
    if not secrets.compare_digest(current.code_hash, hash_code(email, code)):
        return False
    consumed = db.execute(
        update(LoginCode)
        .where(LoginCode.id == current.id, LoginCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    return consumed.rowcount == 1


def csrf_token(request: Request) -> str:
    token = request.session.setdefault("csrf", secrets.token_urlsafe(16))
    return token


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def require_csrf(request: Request) -> None:
    """Router dependency: 403 unless a mutating request's form carries the
    session's CSRF token."""
    if request.method in SAFE_METHODS:
        return
    form = await request.form()
    token = str(form.get("csrf", ""))
    expected = request.session.get("csrf")
    if expected is None or not secrets.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def send_login_code(
    db: Session, mailer: Mailer, email: str, background: BackgroundTasks
) -> None:
    """Issue a code and enqueue the email, which is sent after the response;
    silently sends nothing during cooldown or past the volume caps."""
    now = utcnow()
    if cooldown_active(db, email, now) or volume_capped(db, now):
        return
    code = issue_login_code(db, email, now)
    background.add_task(
        mailer.send,
        to=email,
        subject=f"{code} is your bokehbowl code",
        body=(
            f"Your bokehbowl sign-in code is: {code}\n\n"
            f"It expires in 10 minutes. If you didn't request this, ignore this email."
        ),
    )
