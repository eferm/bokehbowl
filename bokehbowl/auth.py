"""Login codes (email OTP), CSRF tokens, and session access."""

import secrets
from datetime import datetime

from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from bokehbowl.db import LoginCode, utcnow
from bokehbowl.mailer import Mailer


def consume_login_code(db: Session, email: str, code: str, now: datetime) -> bool:
    """Consume and accept the latest unexpired code when its hash matches."""
    current = db.scalar(
        select(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    )
    if current is None or now > current.expires_at:
        return False
    if not current.matches(code):
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
    """Issue a code and enqueue its email after the request commits."""
    now = utcnow()
    login_code, code = LoginCode.issue(email, now)
    db.add(login_code)
    background.add_task(
        mailer.send,
        to=email,
        subject=f"{code} is your bokehbowl code",
        body=(
            f"Your bokehbowl sign-in code is: {code}\n\n"
            f"It expires in 10 minutes. If you didn't request this, ignore this email."
        ),
    )
