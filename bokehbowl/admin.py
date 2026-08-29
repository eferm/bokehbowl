"""Admin routes: raw table views over the database, behind a password login."""

import csv
import io
import secrets
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Annotated, ClassVar, Literal, Self, override

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from bokehbowl.auth import require_csrf
from bokehbowl.db import (
    Address,
    AddressComponents,
    AdminSession,
    Base,
    Edition,
    Mailpiece,
    MailpieceDraft,
    MailReturn,
    NormalizedAddress,
    User,
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


@dataclass(frozen=True)
class TableView:
    model: type[Base]
    timestamp: InstrumentedAttribute[datetime]
    derived_properties: tuple[str, ...] = ()


TABLES: dict[str, TableView] = {
    "users": TableView(
        User,
        User.created_at,
        ("current_address", "current_normalized_address"),
    ),
    "addresses": TableView(Address, Address.created_at),
    "normalized_addresses": TableView(NormalizedAddress, NormalizedAddress.created_at),
    "editions": TableView(Edition, Edition.created_at, ("sent_mailpieces",)),
    "mailpieces": TableView(Mailpiece, Mailpiece.sent_at),
    "returns": TableView(MailReturn, MailReturn.received_at),
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


def require_mailpiece_draft(
    _: AdminOnly,
    db: Db,
    edition_id: str,
    draft_id: str,
) -> MailpieceDraft:
    draft = db.scalar(
        select(MailpieceDraft).where(
            MailpieceDraft.id == draft_id,
            MailpieceDraft.edition_id == edition_id,
        )
    )
    if draft is None:
        raise HTTPException(status_code=404)
    return draft


def require_address(_: AdminOnly, db: Db, address_id: str) -> Address:
    address = db.get(Address, address_id)
    if address is None:
        raise HTTPException(status_code=404)
    return address


UserById = Annotated[User, Depends(require_user)]
EditionById = Annotated[Edition, Depends(require_edition)]
MailpieceById = Annotated[Mailpiece, Depends(require_mailpiece)]
MailpieceDraftByEdition = Annotated[
    MailpieceDraft, Depends(require_mailpiece_draft)
]
AddressById = Annotated[Address, Depends(require_address)]


def require_table(name: str) -> TableView:
    if name not in TABLES:
        raise HTTPException(status_code=404)
    return TABLES[name]


def table_data(
    db: Session,
    view: TableView,
) -> tuple[list[str], list[list[object]]]:
    """Stored columns and configured property values for each row."""
    columns = [
        *(column.key for column in view.model.__table__.columns),
        *view.derived_properties,
    ]
    records = list(db.scalars(select(view.model).order_by(view.timestamp.desc())))
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
    view = require_table(table)
    columns, rows = table_data(db, view)
    counts = {
        name: db.scalar(select(func.count()).select_from(view.model))
        for name, view in TABLES.items()
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
def unsubscribe(user: UserById):
    user.unsubscribe(utcnow())
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/resubscribe")
def resubscribe(user: UserById):
    user.resubscribe()
    return RedirectResponse("/admin", status_code=303)


@router.get("/export.csv")
def export(db: Db, _: AdminOnly, table: str = "users"):
    view = require_table(table)
    columns, rows = table_data(db, view)
    return csv_response(f"bokehbowl-{table}.csv", columns, rows)


@dataclass(frozen=True)
class MailpieceDraftRow:
    draft: MailpieceDraft

    @property
    def user(self) -> User:
        return self.draft.user

    @property
    def address(self) -> Address:
        return self.user.current_address

    @property
    def mailing_address(self) -> Address | NormalizedAddress:
        return self.address


@dataclass(frozen=True)
class ReviewMailpieceDraftRow(MailpieceDraftRow):
    kind: ClassVar[Literal["review"]] = "review"


@dataclass(frozen=True)
class ReadyMailpieceDraftRow(MailpieceDraftRow):
    """A draft whose current address has a printable version."""

    normalized_address: NormalizedAddress
    kind: ClassVar[Literal["ready"]] = "ready"

    @property
    @override
    def mailing_address(self) -> NormalizedAddress:
        return self.normalized_address


@dataclass(frozen=True)
class UnsubscribedMailpieceDraftRow(MailpieceDraftRow):
    kind: ClassVar[Literal["unsubscribed"]] = "unsubscribed"


DraftMailpieceRow = (
    ReviewMailpieceDraftRow
    | ReadyMailpieceDraftRow
    | UnsubscribedMailpieceDraftRow
)


def mailpiece_draft_row(draft: MailpieceDraft) -> DraftMailpieceRow:
    user = draft.user
    normalized_address = user.sendable_normalized_address
    if user.unsubscribed_at is not None:
        return UnsubscribedMailpieceDraftRow(draft)
    if normalized_address is None:
        return ReviewMailpieceDraftRow(draft)
    return ReadyMailpieceDraftRow(draft, normalized_address)


@dataclass(frozen=True)
class MailpieceRow:
    mailpiece: Mailpiece

    @property
    def user(self) -> User:
        return self.mailpiece.user

    @property
    def mailing_address(self) -> NormalizedAddress:
        return self.mailpiece.normalized_address


@dataclass(frozen=True)
class SentMailpieceRow(MailpieceRow):
    kind: ClassVar[Literal["sent"]] = "sent"


@dataclass(frozen=True)
class ReturnedMailpieceRow(MailpieceRow):
    kind: ClassVar[Literal["returned"]] = "returned"


def mailpiece_row(mailpiece: Mailpiece) -> SentMailpieceRow | ReturnedMailpieceRow:
    if mailpiece.mail_return is None:
        return SentMailpieceRow(mailpiece)
    return ReturnedMailpieceRow(mailpiece)


EditionMailingRow = DraftMailpieceRow | SentMailpieceRow | ReturnedMailpieceRow


@dataclass(frozen=True)
class EditionMailpiecesView:
    """An edition's drafts and recorded physical mailpieces."""

    rows: list[EditionMailingRow]

    @property
    def edition_user_ids(self) -> set[str]:
        return {row.user.id for row in self.rows}

    @property
    def ready_drafts(self) -> list[ReadyMailpieceDraftRow]:
        return [row for row in self.rows if isinstance(row, ReadyMailpieceDraftRow)]

    @classmethod
    def from_edition(cls, db: Session, edition: Edition) -> Self:
        drafts = list(
            db.scalars(
                select(MailpieceDraft)
                .join(User)
                .where(MailpieceDraft.edition_id == edition.id)
                .order_by(User.created_at, User.id, MailpieceDraft.id)
            )
        )
        mailpieces = list(
            db.scalars(
                select(Mailpiece)
                .where(Mailpiece.edition_id == edition.id)
                .order_by(Mailpiece.sent_at.desc())
            )
        )
        rows: list[EditionMailingRow] = sorted(
            [
                *(mailpiece_draft_row(draft) for draft in drafts),
                *(mailpiece_row(mailpiece) for mailpiece in mailpieces),
            ],
            key=lambda row: (row.user.created_at, row.user.id),
            reverse=True,
        )
        return cls(rows=rows)


@router.post("/editions")
def create_edition(
    db: Db,
    _: AdminOnly,
    title: Annotated[str, Form()],
):
    edition = Edition(
        title=title.strip(),
        drafts=[
            MailpieceDraft(user=user)
            for user in db.scalars(
                select(User)
                .where(User.unsubscribed_at.is_(None))
                .order_by(User.created_at)
            )
        ],
    )
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
    request: Request,
    db: Db,
    templates: Templates,
    edition: EditionById,
):
    return templates.TemplateResponse(
        request,
        "edition.html",
        {
            "edition": edition,
            "mailing": EditionMailpiecesView.from_edition(db, edition),
            "draft_candidates": list(
                db.scalars(
                    select(User)
                    .where(User.unsubscribed_at.is_(None))
                    .order_by(User.created_at.desc(), User.id.desc())
                )
            ),
        },
    )


@router.post("/editions/{edition_id}/drafts")
def create_mailpiece_draft(
    db: Db,
    edition: EditionById,
    user_id: Annotated[str, Form()],
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404)
    if user.unsubscribed_at is not None:
        raise HTTPException(status_code=409)
    db.add(MailpieceDraft(edition=edition, user=user))
    return RedirectResponse(f"/admin/editions/{edition.id}", status_code=303)


@router.post("/editions/{edition_id}/drafts/{draft_id}/remove")
def remove_mailpiece_draft(
    db: Db,
    edition: EditionById,
    draft: MailpieceDraftByEdition,
):
    db.delete(draft)
    return RedirectResponse(f"/admin/editions/{edition.id}", status_code=303)


@router.post("/editions/{edition_id}/drafts/{draft_id}/mark-sent")
def mark_sent(
    request: Request,
    db: Db,
    edition: EditionById,
    draft: MailpieceDraftByEdition,
):
    user = draft.user
    if user.unsubscribed_at is not None:
        raise HTTPException(status_code=409)
    normalized = user.sendable_normalized_address
    if normalized is None:
        raise HTTPException(status_code=409)
    now = utcnow()
    sent_on = request.app.state.config.operator_date(now)
    consumed_draft_id = db.scalar(
        delete(MailpieceDraft)
        .where(MailpieceDraft.id == draft.id)
        .returning(MailpieceDraft.id)
    )
    if consumed_draft_id is None:
        raise HTTPException(status_code=409)
    db.add(
        Mailpiece(
            edition=edition,
            user=user,
            normalized_address=normalized,
            sent_at=now,
            sent_on=sent_on,
        )
    )
    return RedirectResponse(f"/admin/editions/{edition.id}", status_code=303)


@router.post("/mailpieces/{mailpiece_id}/mark-returned")
def mark_returned(db: Db, mailpiece: MailpieceById):
    if mailpiece.mail_return is not None:
        raise HTTPException(status_code=409)
    db.add(MailReturn(mailpiece=mailpiece))
    db.add(MailpieceDraft(edition=mailpiece.edition, user=mailpiece.user))
    return RedirectResponse(f"/admin/editions/{mailpiece.edition_id}", status_code=303)


@router.get("/editions/{edition_id}/labels.csv")
def export_labels(db: Db, edition: EditionById):
    columns = [field.name for field in fields(AddressComponents)]
    rows = [
        [getattr(draft.normalized_address, column) for column in columns]
        for draft in EditionMailpiecesView.from_edition(db, edition).ready_drafts
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
