"""Admin routes: raw table views over the database, behind a password login."""

import csv
import io
import secrets
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bokehbowl.auth import csrf_token, require_csrf
from bokehbowl.db import (
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


router = APIRouter(prefix="/admin")

TABLES: dict[str, type[Base]] = {
    "recipients": Recipient,
    "recipient_versions": RecipientVersion,
    "mailings": Mailing,
    "mailpieces": Mailpiece,
}


def require_admin(request: Request) -> None:
    if not request.session.get("is_admin"):
        raise AdminRequired()


def require_table(name: str) -> type[Base]:
    if name not in TABLES:
        raise HTTPException(status_code=404)
    return TABLES[name]


def columns_of(model: type[Base]) -> list[str]:
    return [column.key for column in model.__table__.columns]


def rows_of(db: Session, model: type[Base]) -> list[list[object]]:
    columns = columns_of(model)
    objects = db.scalars(select(model).order_by(model.id.desc()))
    return [[getattr(obj, column) for column in columns] for obj in objects]


@router.get("/login")
def login_form(request: Request, templates: Templates):
    return templates.TemplateResponse(
        request, "admin_login.html", {"csrf": csrf_token(request), "error": None}
    )


@router.post("/login")
def login(
    request: Request,
    templates: Templates,
    csrf: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    require_csrf(request, csrf)
    expected = request.app.state.config.admin_password
    if not secrets.compare_digest(password, expected):
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"csrf": csrf_token(request), "error": "Wrong password."},
            status_code=401,
        )
    request.session["is_admin"] = True
    return RedirectResponse("/admin", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: Annotated[str, Form()]):
    require_csrf(request, csrf)
    request.session.pop("is_admin", None)
    return RedirectResponse("/admin/login", status_code=303)


@router.get("")
def dashboard(
    request: Request, db: Db, templates: Templates, table: str = "recipients"
):
    require_admin(request)
    model = require_table(table)
    counts = {
        name: db.scalar(select(func.count()).select_from(m))
        for name, m in TABLES.items()
    }
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "csrf": csrf_token(request),
            "table": table,
            "columns": columns_of(model),
            "rows": rows_of(db, model),
            "counts": counts,
        },
    )


@router.post("/recipients/{recipient_id}/unregister")
def unregister(
    request: Request, db: Db, recipient_id: int, csrf: Annotated[str, Form()]
):
    require_admin(request)
    require_csrf(request, csrf)
    recipient = db.get(Recipient, recipient_id)
    if recipient is None:
        raise HTTPException(status_code=404)
    if recipient.unsubscribed_at is None:
        recipient.unsubscribed_at = utcnow()
        db.add(recipient)
    return RedirectResponse("/admin", status_code=303)


@router.post("/recipients/{recipient_id}/reregister")
def reregister(
    request: Request, db: Db, recipient_id: int, csrf: Annotated[str, Form()]
):
    require_admin(request)
    require_csrf(request, csrf)
    recipient = db.get(Recipient, recipient_id)
    if recipient is None:
        raise HTTPException(status_code=404)
    recipient.unsubscribed_at = None
    db.add(recipient)
    return RedirectResponse("/admin", status_code=303)


@router.get("/export.csv")
def export(request: Request, db: Db, table: str = "recipients"):
    require_admin(request)
    model = require_table(table)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns_of(model))
    writer.writerows(rows_of(db, model))
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=bokehbowl-{table}.csv"},
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


def require_mailing(db: Session, mailing_id: int) -> Mailing:
    mailing = db.get(Mailing, mailing_id)
    if mailing is None:
        raise HTTPException(status_code=404)
    return mailing


def mailpieces_of(db: Session, mailing_id: int) -> list[Mailpiece]:
    return list(
        db.scalars(
            select(Mailpiece)
            .where(Mailpiece.mailing_id == mailing_id)
            .order_by(Mailpiece.sent_at.desc())
        )
    )


def unsent_recipients(db: Session, mailing_id: int) -> list[Recipient]:
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
    request: Request,
    db: Db,
    csrf: Annotated[str, Form()],
    title: Annotated[str, Form()],
):
    require_admin(request)
    require_csrf(request, csrf)
    mailing = Mailing(title=title.strip())
    db.add(mailing)
    db.flush()
    return RedirectResponse(f"/admin/mailings/{mailing.id}", status_code=303)


@router.get("/mailings/{mailing_id}")
def mailing_detail(request: Request, db: Db, templates: Templates, mailing_id: int):
    require_admin(request)
    mailing = require_mailing(db, mailing_id)
    return templates.TemplateResponse(
        request,
        "mailing.html",
        {
            "csrf": csrf_token(request),
            "mailing": mailing,
            "pending": pending_recipients(db, mailing),
            "late": late_recipients(db, mailing),
            "mailpieces": mailpieces_of(db, mailing_id),
        },
    )


@router.post("/mailings/{mailing_id}/send/{recipient_id}")
def mark_sent(
    request: Request,
    db: Db,
    mailing_id: int,
    recipient_id: int,
    csrf: Annotated[str, Form()],
):
    require_admin(request)
    require_csrf(request, csrf)
    mailing = require_mailing(db, mailing_id)
    version = latest_version(db, recipient_id)
    if version is None:
        raise HTTPException(status_code=404)
    already_sent = db.scalar(
        select(Mailpiece).where(
            Mailpiece.mailing_id == mailing.id, Mailpiece.recipient_id == recipient_id
        )
    )
    if already_sent is None:
        db.add(
            Mailpiece(
                mailing_id=mailing.id,
                recipient_id=recipient_id,
                recipient_version_id=version.id,
                sent_at=utcnow(),
            )
        )
    return RedirectResponse(f"/admin/mailings/{mailing.id}", status_code=303)


@router.post("/mailpieces/{mailpiece_id}/delete")
def undo_mailpiece(
    request: Request, db: Db, mailpiece_id: int, csrf: Annotated[str, Form()]
):
    require_admin(request)
    require_csrf(request, csrf)
    mailpiece = db.get(Mailpiece, mailpiece_id)
    if mailpiece is None:
        raise HTTPException(status_code=404)
    mailing_id = mailpiece.mailing_id
    db.delete(mailpiece)
    return RedirectResponse(f"/admin/mailings/{mailing_id}", status_code=303)


@router.get("/mailings/{mailing_id}/labels.csv")
def export_labels(request: Request, db: Db, mailing_id: int):
    require_admin(request)
    mailing = require_mailing(db, mailing_id)
    columns = [
        "name",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for recipient in pending_recipients(db, mailing):
        writer.writerow([getattr(recipient, column) for column in columns])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=mailing-{mailing_id}-to-send.csv"
            )
        },
    )
