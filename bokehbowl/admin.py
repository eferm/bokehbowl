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
    Postcard,
    Recipient,
    RecipientVersion,
    Sending,
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
    "postcards": Postcard,
    "sendings": Sending,
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
    """Everyone a postcard may be sent to: verified and not unregistered."""
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


def require_postcard(db: Session, postcard_id: int) -> Postcard:
    postcard = db.get(Postcard, postcard_id)
    if postcard is None:
        raise HTTPException(status_code=404)
    return postcard


def postcard_sendings(db: Session, postcard_id: int) -> list[Sending]:
    return list(
        db.scalars(
            select(Sending)
            .where(Sending.postcard_id == postcard_id)
            .order_by(Sending.sent_at.desc())
        )
    )


def unsent_recipients(db: Session, postcard_id: int) -> list[Recipient]:
    sent_ids = {sending.recipient_id for sending in postcard_sendings(db, postcard_id)}
    return [r for r in eligible_recipients(db) if r.id not in sent_ids]


def pending_recipients(db: Session, postcard: Postcard) -> list[Recipient]:
    """The default mailing list: unsent recipients who existed when the postcard did."""
    return [
        r
        for r in unsent_recipients(db, postcard.id)
        if r.created_at <= postcard.created_at
    ]


def late_recipients(db: Session, postcard: Postcard) -> list[Recipient]:
    """Unsent recipients who signed up after the postcard was created — sendable
    only by explicit choice."""
    return [
        r
        for r in unsent_recipients(db, postcard.id)
        if r.created_at > postcard.created_at
    ]


@router.post("/postcards")
def create_postcard(
    request: Request,
    db: Db,
    csrf: Annotated[str, Form()],
    title: Annotated[str, Form()],
):
    require_admin(request)
    require_csrf(request, csrf)
    postcard = Postcard(title=title.strip())
    db.add(postcard)
    db.flush()
    return RedirectResponse(f"/admin/postcards/{postcard.id}", status_code=303)


@router.get("/postcards/{postcard_id}")
def postcard_detail(request: Request, db: Db, templates: Templates, postcard_id: int):
    require_admin(request)
    postcard = require_postcard(db, postcard_id)
    return templates.TemplateResponse(
        request,
        "postcard.html",
        {
            "csrf": csrf_token(request),
            "postcard": postcard,
            "pending": pending_recipients(db, postcard),
            "late": late_recipients(db, postcard),
            "sendings": postcard_sendings(db, postcard_id),
        },
    )


@router.post("/postcards/{postcard_id}/send/{recipient_id}")
def mark_sent(
    request: Request,
    db: Db,
    postcard_id: int,
    recipient_id: int,
    csrf: Annotated[str, Form()],
):
    require_admin(request)
    require_csrf(request, csrf)
    postcard = require_postcard(db, postcard_id)
    version = latest_version(db, recipient_id)
    if version is None:
        raise HTTPException(status_code=404)
    already_sent = db.scalar(
        select(Sending).where(
            Sending.postcard_id == postcard.id, Sending.recipient_id == recipient_id
        )
    )
    if already_sent is None:
        db.add(
            Sending(
                postcard_id=postcard.id,
                recipient_id=recipient_id,
                recipient_version_id=version.id,
                sent_at=utcnow(),
            )
        )
    return RedirectResponse(f"/admin/postcards/{postcard.id}", status_code=303)


@router.post("/sendings/{sending_id}/delete")
def undo_sending(
    request: Request, db: Db, sending_id: int, csrf: Annotated[str, Form()]
):
    require_admin(request)
    require_csrf(request, csrf)
    sending = db.get(Sending, sending_id)
    if sending is None:
        raise HTTPException(status_code=404)
    postcard_id = sending.postcard_id
    db.delete(sending)
    return RedirectResponse(f"/admin/postcards/{postcard_id}", status_code=303)


@router.get("/postcards/{postcard_id}/labels.csv")
def export_labels(request: Request, db: Db, postcard_id: int):
    require_admin(request)
    postcard = require_postcard(db, postcard_id)
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
    for recipient in pending_recipients(db, postcard):
        writer.writerow([getattr(recipient, column) for column in columns])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=postcard-{postcard_id}-to-send.csv"
            )
        },
    )
