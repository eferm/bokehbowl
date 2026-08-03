"""Admin routes: raw table views over the database, behind a password login."""

import csv
import io
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Self

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
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


TABLES: dict[str, tuple[type[Base], InstrumentedAttribute[datetime]]] = {
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
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename={filename}",
        },
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
    edition = db.scalar(
        select(Edition).where(
            Edition.id == edition_id,
            Edition.deleted_at.is_(None),
        )
    )
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


def require_table(
    name: str,
) -> tuple[type[Base], InstrumentedAttribute[datetime]]:
    if name not in TABLES:
        raise HTTPException(status_code=404)
    return TABLES[name]


def table_data(
    db: Session,
    model: type[Base],
    timestamp: InstrumentedAttribute[datetime],
    include_derived: bool = False,
) -> tuple[list[str], list[list[object]]]:
    """Stored columns and property-backed derived columns for each row."""
    columns = [
        *(column.key for column in model.__table__.columns),
        *(model.derived_property_names() if include_derived else ()),
    ]
    records = list(db.scalars(select(model).order_by(timestamp.desc())))
    return columns, [[getattr(row, column) for column in columns] for row in records]


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
    # The generated id is needed in the browser session before the response.
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
    table: str = "users",
):
    model, timestamp = require_table(table)
    columns, rows = table_data(
        db,
        model,
        timestamp,
        include_derived=True,
    )
    counts = {
        name: db.scalar(select(func.count()).select_from(model))
        for name, (model, _) in TABLES.items()
    }
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "table": table,
            "columns": columns,
            "rows": rows,
            "counts": counts,
        },
    )


@router.get("/users/{user_id}/unsubscribe")
def confirm_unsubscribe(request: Request, templates: Templates, user: UserById):
    return templates.TemplateResponse(
        request,
        "unsubscribe_user.html",
        {"user": user},
    )


@router.post("/users/{user_id}/unsubscribe")
def unsubscribe(db: Db, user: UserById):
    user.unsubscribe(utcnow())
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/resubscribe")
def resubscribe(user: UserById):
    user.resubscribe()
    return RedirectResponse("/admin", status_code=303)


@router.get("/export.csv")
def export(db: Db, _: AdminOnly, table: str = "users"):
    model, timestamp = require_table(table)
    columns, rows = table_data(db, model, timestamp)
    return csv_response(f"bokehbowl-{table}.csv", columns, rows)


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


@dataclass(frozen=True)
class RecipientGroup:
    """Unsent recipients grouped by whether their current address is ready to
    print."""

    review: list[ReviewRecipient]
    ready: list[ReadyRecipient]

    @property
    def count(self) -> int:
        return len(self.review) + len(self.ready)

    @classmethod
    def from_users(cls, users: Iterable[User]) -> Self:
        review: list[ReviewRecipient] = []
        ready: list[ReadyRecipient] = []
        for user in users:
            address = user.current_address
            normalized_address = user.current_normalized_address
            if normalized_address is None:
                review.append(ReviewRecipient(user, address))
            else:
                ready.append(ReadyRecipient(user, address, normalized_address))
        return cls(review, ready)


@dataclass(frozen=True)
class EditionMailingView:
    """The complete derived mailing workflow for one edition."""

    base: RecipientGroup
    late: RecipientGroup
    sent: list[Mailpiece]

    @classmethod
    def from_edition(cls, db: Session, edition: Edition) -> Self:
        """Build the edition's base, late-signup, and sent recipient groups.

        Subscription is live, the original/catch-up boundary is the edition's
        creation time, and recorded mailpieces are historical regardless of
        current subscription.
        """
        sent = list(
            db.scalars(
                select(Mailpiece)
                .where(Mailpiece.edition_id == edition.id)
                .order_by(Mailpiece.sent_at.desc())
            )
        )
        unsent = [
            user
            for user in db.scalars(
                select(User)
                .where(User.unsubscribed_at.is_(None))
                .order_by(User.created_at)
            )
            if user.id not in {mailpiece.user_id for mailpiece in sent}
        ]
        base_users = [user for user in unsent if user.created_at <= edition.created_at]
        late_users = [user for user in unsent if user.created_at > edition.created_at]
        return cls(
            base=RecipientGroup.from_users(base_users),
            late=RecipientGroup.from_users(reversed(late_users)),
            sent=sent,
        )


@router.post("/editions")
def create_edition(
    db: Db,
    _: AdminOnly,
    title: Annotated[str, Form()],
):
    edition = Edition(title=title.strip())
    db.add(edition)
    # The generated id is needed in the redirect URL before the response.
    db.flush()
    return RedirectResponse(f"/admin/editions/{edition.id}", status_code=303)


@router.get("/editions/{edition_id}/delete")
def confirm_delete_edition(
    request: Request, templates: Templates, edition: EditionById
):
    return templates.TemplateResponse(
        request,
        "delete_edition.html",
        {"edition": edition},
    )


@router.post("/editions/{edition_id}/delete")
def delete_edition(edition: EditionById):
    edition.archive(utcnow())
    return RedirectResponse("/admin?table=editions", status_code=303)


@router.get("/editions/{edition_id}")
def edition_detail(
    request: Request, db: Db, templates: Templates, edition: EditionById
):
    return templates.TemplateResponse(
        request,
        "edition.html",
        {
            "edition": edition,
            "mailing": EditionMailingView.from_edition(db, edition),
        },
    )


@router.post("/editions/{edition_id}/send/{user_id}")
def mark_sent(
    db: Db,
    edition: EditionById,
    user: UserById,
    normalized_address_id: Annotated[str, Form()],
):
    # The bulk list has a creation-time cutoff, but this route also serves an
    # operator's explicit catch-up send. A live subscription is the shared
    # eligibility rule for both paths.
    if user.unsubscribed_at is not None:
        raise HTTPException(status_code=409)
    normalized = db.get(NormalizedAddress, normalized_address_id)
    if normalized is None or normalized.address.user_id != user.id:
        raise HTTPException(status_code=404)
    already_sent = db.scalar(
        select(Mailpiece.id).where(
            Mailpiece.edition_id == edition.id,
            Mailpiece.user_id == user.id,
        )
    )
    if already_sent is None:
        edition.mailpieces.append(
            Mailpiece(
                user=user,
                normalized_address=normalized,
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
        for recipient in EditionMailingView.from_edition(db, edition).base.ready
    ]
    return csv_response(f"edition-{edition.id}-to-send.csv", columns, rows)


@router.get("/editions/{edition_id}/labels-late.csv")
def export_late_labels(
    db: Db,
    edition: EditionById,
    user_id: Annotated[list[str] | None, Query()] = None,
):
    selected_ids = set(user_id or ())
    columns = list(ADDRESS_FIELDS)
    rows = [
        [getattr(recipient.normalized_address, column) for column in columns]
        for recipient in EditionMailingView.from_edition(db, edition).late.ready
        if recipient.user.id in selected_ids
    ]
    return csv_response(f"edition-{edition.id}-late-to-send.csv", columns, rows)


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
            "current": address.current_normalized_address or address,
            "edition": edition,
        },
    )


@router.post("/addresses/{address_id}/normalize")
def normalize_address(
    address: AddressById,
    form: Annotated[NormalizeForm, Form()],
):
    address.record_normalized_address(form.components)
    destination = f"/admin/editions/{form.edition}" if form.edition else "/admin"
    return RedirectResponse(destination, status_code=303)
