"""Admin routes: raw table views over the database, behind a password login."""

import csv
import io
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from bokehbowl.auth import require_csrf
from bokehbowl.db import (
    ADDRESS_FIELDS,
    Address,
    AdminSession,
    Base,
    Edition,
    Mailpiece,
    NormalizedAddress,
    User,
    UserSession,
    latest_address,
    latest_normalized_address,
    record_normalized_address,
    utcnow,
)
from bokehbowl.web import AddressForm, Db, Templates


class AdminRequired(Exception):
    """Raised when an admin page is hit without an admin session."""


class NormalizeForm(AddressForm):
    """A print-version submission, carrying the edition page to return to."""

    edition: str = ""


router = APIRouter(prefix="/admin", dependencies=[Depends(require_csrf)])

ADMIN_SESSION_TTL = timedelta(days=14)


TABLES: dict[str, tuple[type[Base], InstrumentedAttribute]] = {
    "users": (User, User.created_at),
    "addresses": (Address, Address.created_at),
    "normalized_addresses": (NormalizedAddress, NormalizedAddress.created_at),
    "editions": (Edition, Edition.created_at),
    "mailpieces": (Mailpiece, Mailpiece.sent_at),
}


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


def require_user(_: AdminOnly, db: Db, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    return user


def require_edition(_: AdminOnly, db: Db, edition_id: str) -> Edition:
    edition = db.get(Edition, edition_id)
    if edition is None:
        raise HTTPException(status_code=404)
    return edition


def require_mailpiece(_: AdminOnly, db: Db, mailpiece_id: str) -> Mailpiece:
    mailpiece = db.get(Mailpiece, mailpiece_id)
    if mailpiece is None:
        raise HTTPException(status_code=404)
    return mailpiece


def require_address(_: AdminOnly, db: Db, address_id: str) -> Address:
    address = db.get(Address, address_id)
    if address is None:
        raise HTTPException(status_code=404)
    return address


UserById = Annotated[User, Depends(require_user)]
EditionById = Annotated[Edition, Depends(require_edition)]
MailpieceById = Annotated[Mailpiece, Depends(require_mailpiece)]
AddressById = Annotated[Address, Depends(require_address)]


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
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@router.post("/login")
def login(
    request: Request,
    db: Db,
    templates: Templates,
    password: Annotated[str, Form()],
):
    now = utcnow()
    expected = request.app.state.config.admin_password
    if not secrets.compare_digest(password, expected):
        return templates.TemplateResponse(
            request,
            "admin_login.html",
            {"error": "Wrong password."},
            status_code=401,
        )
    db.execute(
        delete(AdminSession).where(AdminSession.created_at < now - ADMIN_SESSION_TTL)
    )
    session = AdminSession()
    db.add(session)
    db.flush()
    request.session["admin_session_id"] = session.id
    db.commit()
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
    table: str = "users",
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
            "table": table,
            "columns": columns_of(model),
            "rows": rows_of(db, model, timestamp),
            "counts": counts,
        },
    )


@router.post("/users/{user_id}/unsubscribe")
def unsubscribe(db: Db, user: UserById):
    if user.unsubscribed_at is None:
        user.unsubscribed_at = utcnow()
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/resubscribe")
def resubscribe(db: Db, user: UserById):
    user.unsubscribed_at = None
    return RedirectResponse("/admin", status_code=303)


@router.get("/export.csv")
def export(db: Db, _: AdminOnly, table: str = "users"):
    model, timestamp = require_table(table)
    return csv_response(
        f"bokehbowl-{table}.csv", columns_of(model), rows_of(db, model, timestamp)
    )


def eligible_users(db: Session, edition: Edition) -> list[User]:
    """Everyone the edition may be sent to: still subscribed, and signed up by
    the time the edition was created. The one definition of who is on an
    edition's list; the edition page and the send handler both ask it."""
    return list(
        db.scalars(
            select(User)
            .where(
                User.unsubscribed_at.is_(None),
                User.created_at <= edition.created_at,
            )
            .order_by(User.created_at)
        )
    )


def mailpieces_of(db: Session, edition_id: str) -> list[Mailpiece]:
    return list(
        db.scalars(
            select(Mailpiece)
            .where(Mailpiece.edition_id == edition_id)
            .order_by(Mailpiece.sent_at.desc())
        )
    )


@dataclass(frozen=True)
class ReviewRecipient:
    """A user whose current address awaits a print version."""

    user: User
    address: Address


@dataclass(frozen=True)
class ReadyRecipient:
    """A user whose current address has a print version, which an envelope to
    them prints."""

    user: User
    address: Address
    normalized_address: NormalizedAddress


Recipient = ReviewRecipient | ReadyRecipient
"""An unsent recipient, in the kind their current address makes them."""


def unsent_recipients(db: Session, edition: Edition) -> list[Recipient]:
    """Eligible users the edition has not gone to yet, each as the kind their
    current address makes them."""
    sent_ids = {mailpiece.user_id for mailpiece in mailpieces_of(db, edition.id)}
    recipients: list[Recipient] = []
    for user in eligible_users(db, edition):
        if user.id in sent_ids:
            continue
        address = latest_address(db, user.id)
        normalized_address = latest_normalized_address(db, address)
        recipients.append(
            ReadyRecipient(user, address, normalized_address)
            if normalized_address is not None
            else ReviewRecipient(user, address)
        )
    return recipients


def recipients_ready_to_send(recipients: list[Recipient]) -> list[ReadyRecipient]:
    """Recipients whose current address has a print version."""
    return [
        recipient for recipient in recipients if isinstance(recipient, ReadyRecipient)
    ]


def recipients_needing_review(recipients: list[Recipient]) -> list[ReviewRecipient]:
    """Recipients whose current address awaits a print version."""
    return [
        recipient for recipient in recipients if isinstance(recipient, ReviewRecipient)
    ]


@router.post("/editions")
def create_edition(
    db: Db,
    _: AdminOnly,
    title: Annotated[str, Form()],
):
    edition = Edition(title=title.strip())
    db.add(edition)
    db.flush()
    return RedirectResponse(f"/admin/editions/{edition.id}", status_code=303)


@router.get("/editions/{edition_id}")
def edition_detail(
    request: Request, db: Db, templates: Templates, edition: EditionById
):
    unsent = unsent_recipients(db, edition)
    return templates.TemplateResponse(
        request,
        "edition.html",
        {
            "edition": edition,
            "to_send": recipients_ready_to_send(unsent),
            "needs_review": recipients_needing_review(unsent),
            "mailpieces": mailpieces_of(db, edition.id),
        },
    )


@router.post("/editions/{edition_id}/send/{user_id}")
def mark_sent(
    db: Db,
    edition: EditionById,
    user: UserById,
    normalized_address_id: Annotated[str, Form()],
):
    if user.id not in {eligible.id for eligible in eligible_users(db, edition)}:
        raise HTTPException(status_code=409)
    normalized = db.get(NormalizedAddress, normalized_address_id)
    if normalized is None or normalized.address.user_id != user.id:
        raise HTTPException(status_code=404)
    sent_ids = {mailpiece.user_id for mailpiece in mailpieces_of(db, edition.id)}
    if user.id not in sent_ids:
        db.add(
            Mailpiece(
                edition_id=edition.id,
                user_id=user.id,
                normalized_address_id=normalized.id,
                sent_at=utcnow(),
            )
        )
    return RedirectResponse(f"/admin/editions/{edition.id}", status_code=303)


@router.post("/mailpieces/{mailpiece_id}/delete")
def delete_mailpiece(db: Db, mailpiece: MailpieceById):
    edition_id = mailpiece.edition_id
    db.delete(mailpiece)
    return RedirectResponse(f"/admin/editions/{edition_id}", status_code=303)


@router.get("/editions/{edition_id}/labels.csv")
def export_labels(db: Db, edition: EditionById):
    columns = list(ADDRESS_FIELDS)
    rows = [
        [getattr(recipient.normalized_address, column) for column in columns]
        for recipient in recipients_ready_to_send(unsent_recipients(db, edition))
    ]
    return csv_response(f"edition-{edition.id}-to-send.csv", columns, rows)


@router.get("/addresses/{address_id}/normalize")
def normalize_form(
    request: Request,
    db: Db,
    templates: Templates,
    address: AddressById,
    edition: str = "",
):
    return templates.TemplateResponse(
        request,
        "normalize.html",
        {
            "address": address,
            "current": latest_normalized_address(db, address) or address,
            "edition": edition,
        },
    )


@router.post("/addresses/{address_id}/normalize")
def normalize_address(
    db: Db,
    address: AddressById,
    form: Annotated[NormalizeForm, Form()],
):
    record_normalized_address(db, address, form.components)
    destination = f"/admin/editions/{form.edition}" if form.edition else "/admin"
    return RedirectResponse(destination, status_code=303)
