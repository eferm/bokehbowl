"""Admin routes: raw table views over the database, behind a password login."""

import csv
import io
import secrets
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from bokehbowl.auth import csrf_token, require_csrf
from bokehbowl.db import (
    AdminSession,
    Base,
    Mailing,
    Mailpiece,
    Recipient,
    RecipientVersion,
    latest_version,
    utcnow,
)
from bokehbowl.web import Db, Templates


class AdminRequired(Exception):
    """Raised when an admin page is hit without an admin session."""


router = APIRouter(prefix="/admin", dependencies=[Depends(require_csrf)])

ADMIN_LOGIN_CAP = 10
ADMIN_LOGIN_BACKSTOP = 100
ADMIN_LOGIN_WINDOW = timedelta(minutes=15)
ADMIN_SESSION_TTL = timedelta(days=14)

TABLES: dict[str, tuple[type[Base], InstrumentedAttribute]] = {
    "recipients": (Recipient, Recipient.created_at),
    "recipient_versions": (RecipientVersion, RecipientVersion.valid_from),
    "mailings": (Mailing, Mailing.created_at),
    "mailpieces": (Mailpiece, Mailpiece.sent_at),
}


class LoginThrottle:
    """Failed-login timestamps per client address, with a per-address cap and an
    instance-wide backstop over a sliding window."""

    def __init__(self) -> None:
        self.failures: dict[str, list[datetime]] = {}

    def prune(self, now: datetime) -> None:
        """Drop attempts older than the window from every bucket, discarding
        emptied ones."""
        for address in list(self.failures):
            self.failures[address] = [
                failed_at
                for failed_at in self.failures[address]
                if failed_at > now - ADMIN_LOGIN_WINDOW
            ]
            if not self.failures[address]:
                del self.failures[address]

    def throttled(self, address: str, now: datetime) -> bool:
        """True when the address's bucket is at its cap or the instance total is at
        its backstop, after pruning to the window."""
        self.prune(now)
        total = sum(len(entries) for entries in self.failures.values())
        return (
            len(self.failures.get(address, [])) >= ADMIN_LOGIN_CAP
            or total >= ADMIN_LOGIN_BACKSTOP
        )

    def record(self, address: str, now: datetime) -> None:
        """Append a failed attempt for the address at the given time."""
        self.failures.setdefault(address, []).append(now)


def formula_safe(value: object) -> object:
    """A CSV cell value with spreadsheet formula triggers neutralized."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def csv_response(
    filename: str, columns: list[str], rows: list[list[object]]
) -> Response:
    """A CSV attachment: header row, then formula-neutralized data rows."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows([formula_safe(cell) for cell in row] for row in rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def require_admin(request: Request, db: Db) -> None:
    session_id = request.session.get("admin_session_id")
    if session_id is None:
        raise AdminRequired()
    row = db.get(AdminSession, session_id)
    if row is None or utcnow() - row.created_at > ADMIN_SESSION_TTL:
        raise AdminRequired()


AdminOnly = Annotated[None, Depends(require_admin)]


def require_recipient(_: AdminOnly, db: Db, recipient_id: str) -> Recipient:
    recipient = db.get(Recipient, recipient_id)
    if recipient is None:
        raise HTTPException(status_code=404)
    return recipient


def require_mailing(_: AdminOnly, db: Db, mailing_id: str) -> Mailing:
    mailing = db.get(Mailing, mailing_id)
    if mailing is None:
        raise HTTPException(status_code=404)
    return mailing


def require_mailpiece(_: AdminOnly, db: Db, mailpiece_id: str) -> Mailpiece:
    mailpiece = db.get(Mailpiece, mailpiece_id)
    if mailpiece is None:
        raise HTTPException(status_code=404)
    return mailpiece


RecipientById = Annotated[Recipient, Depends(require_recipient)]
MailingById = Annotated[Mailing, Depends(require_mailing)]
MailpieceById = Annotated[Mailpiece, Depends(require_mailpiece)]


def require_table(name: str) -> tuple[type[Base], InstrumentedAttribute]:
    if name not in TABLES:
        raise HTTPException(status_code=404)
    return TABLES[name]


def columns_of(model: type[Base]) -> list[str]:
    return [column.key for column in model.__table__.columns]


def rows_of(
    db: Session, model: type[Base], timestamp: InstrumentedAttribute
) -> list[list[object]]:
    columns = columns_of(model)
    objects = db.scalars(select(model).order_by(timestamp.desc()))
    return [[getattr(obj, column) for column in columns] for obj in objects]


@router.get("/login")
def login_form(request: Request, templates: Templates):
    return templates.TemplateResponse(
        request, "admin_login.html", {"csrf": csrf_token(request), "error": None}
    )


@router.post("/login")
def login(
    request: Request,
    db: Db,
    templates: Templates,
    password: Annotated[str, Form()],
):
    now = utcnow()
    throttle = request.app.state.admin_login_throttle
    address = request.client.host if request.client else ""
    if throttle.throttled(address, now):
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {
                "csrf": csrf_token(request),
                "error": "Too many attempts. Try again later.",
            },
            status_code=429,
        )
    expected = request.app.state.config.admin_password
    if not secrets.compare_digest(password, expected):
        throttle.record(address, now)
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"csrf": csrf_token(request), "error": "Wrong password."},
            status_code=401,
        )
    db.execute(
        delete(AdminSession).where(AdminSession.created_at < now - ADMIN_SESSION_TTL)
    )
    session = AdminSession()
    db.add(session)
    db.flush()
    request.session["admin_session_id"] = session.id
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
def logout(request: Request, db: Db):
    session_id = request.session.get("admin_session_id")
    if session_id is not None:
        row = db.get(AdminSession, session_id)
        if row is not None:
            db.delete(row)
    request.session.pop("admin_session_id", None)
    return RedirectResponse("/admin/login", status_code=303)


@router.get("")
def dashboard(
    request: Request,
    db: Db,
    templates: Templates,
    _: AdminOnly,
    table: str = "recipients",
):
    model, timestamp = require_table(table)
    counts = {
        name: db.scalar(select(func.count()).select_from(m))
        for name, (m, _) in TABLES.items()
    }
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "csrf": csrf_token(request),
            "table": table,
            "columns": columns_of(model),
            "rows": rows_of(db, model, timestamp),
            "counts": counts,
        },
    )


@router.post("/recipients/{recipient_id}/unregister")
def unregister(db: Db, recipient: RecipientById):
    if recipient.unsubscribed_at is None:
        recipient.unsubscribed_at = utcnow()
        db.add(recipient)
    return RedirectResponse("/admin", status_code=303)


@router.post("/recipients/{recipient_id}/reregister")
def reregister(db: Db, recipient: RecipientById):
    recipient.unsubscribed_at = None
    db.add(recipient)
    return RedirectResponse("/admin", status_code=303)


@router.get("/export.csv")
def export(db: Db, _: AdminOnly, table: str = "recipients"):
    model, timestamp = require_table(table)
    return csv_response(
        f"bokehbowl-{table}.csv", columns_of(model), rows_of(db, model, timestamp)
    )


def eligible_recipients(db: Session) -> list[Recipient]:
    """Everyone a mailing may be sent to: verified and not unregistered."""
    return list(
        db.scalars(
            select(Recipient)
            .where(
                Recipient.verified_at.is_not(None),
                Recipient.unsubscribed_at.is_(None),
            )
            .order_by(Recipient.name)
        )
    )


def mailpieces_of(db: Session, mailing_id: str) -> list[Mailpiece]:
    return list(
        db.scalars(
            select(Mailpiece)
            .where(Mailpiece.mailing_id == mailing_id)
            .order_by(Mailpiece.sent_at.desc())
        )
    )


def unsent_recipients(db: Session, mailing_id: str) -> list[Recipient]:
    sent_ids = {mailpiece.recipient_id for mailpiece in mailpieces_of(db, mailing_id)}
    return [r for r in eligible_recipients(db) if r.id not in sent_ids]


def pending_recipients(db: Session, mailing: Mailing) -> list[Recipient]:
    """The default mailing list: unsent recipients who existed when the mailing did."""
    return [
        r
        for r in unsent_recipients(db, mailing.id)
        if r.created_at <= mailing.created_at
    ]


def late_recipients(db: Session, mailing: Mailing) -> list[Recipient]:
    """Unsent recipients who signed up after the mailing was created — sendable
    only by explicit choice."""
    return [
        r
        for r in unsent_recipients(db, mailing.id)
        if r.created_at > mailing.created_at
    ]


@router.post("/mailings")
def create_mailing(
    db: Db,
    _: AdminOnly,
    title: Annotated[str, Form()],
):
    mailing = Mailing(title=title.strip())
    db.add(mailing)
    db.flush()
    return RedirectResponse(f"/admin/mailings/{mailing.id}", status_code=303)


@router.get("/mailings/{mailing_id}")
def mailing_detail(
    request: Request, db: Db, templates: Templates, mailing: MailingById
):
    return templates.TemplateResponse(
        request,
        "mailing.html",
        {
            "csrf": csrf_token(request),
            "mailing": mailing,
            "pending": pending_recipients(db, mailing),
            "late": late_recipients(db, mailing),
            "mailpieces": mailpieces_of(db, mailing.id),
        },
    )


@router.post("/mailings/{mailing_id}/send/{recipient_id}")
def mark_sent(
    db: Db,
    mailing: MailingById,
    recipient: RecipientById,
):
    if recipient.verified_at is None or recipient.unsubscribed_at is not None:
        raise HTTPException(status_code=409)
    version = latest_version(db, recipient.id)
    if version is None:
        raise HTTPException(status_code=404)
    already_sent = db.scalar(
        select(Mailpiece).where(
            Mailpiece.mailing_id == mailing.id, Mailpiece.recipient_id == recipient.id
        )
    )
    if already_sent is None:
        db.add(
            Mailpiece(
                mailing_id=mailing.id,
                recipient_id=recipient.id,
                recipient_version_id=version.id,
                sent_at=utcnow(),
            )
        )
    return RedirectResponse(f"/admin/mailings/{mailing.id}", status_code=303)


@router.post("/mailpieces/{mailpiece_id}/delete")
def undo_mailpiece(db: Db, mailpiece: MailpieceById):
    mailing_id = mailpiece.mailing_id
    db.delete(mailpiece)
    return RedirectResponse(f"/admin/mailings/{mailing_id}", status_code=303)


@router.get("/mailings/{mailing_id}/labels.csv")
def export_labels(db: Db, mailing: MailingById):
    columns = [
        "name",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country",
    ]
    rows = [
        [getattr(recipient, column) for column in columns]
        for recipient in pending_recipients(db, mailing)
    ]
    return csv_response(f"mailing-{mailing.id}-to-send.csv", columns, rows)
