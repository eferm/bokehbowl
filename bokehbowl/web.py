"""Public routes: signup, sign-in via email code, and the account page."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from bokehbowl.auth import consume_login_code, csrf_token, require_csrf, send_login_code
from bokehbowl.db import Recipient, record_version, utcnow
from bokehbowl.mailer import Mailer


class LoginRequired(Exception):
    """Raised when a page needs a signed-in recipient and the session has none."""


def get_db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as db:
        yield db
        db.commit()


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


Db = Annotated[Session, Depends(get_db)]
Templates = Annotated[Jinja2Templates, Depends(get_templates)]
Mail = Annotated[Mailer, Depends(get_mailer)]


def require_recipient(request: Request, db: Db) -> Recipient:
    recipient_id = request.session.get("recipient_id")
    if recipient_id is None:
        raise LoginRequired()
    recipient = db.get(Recipient, recipient_id)
    if recipient is None:
        raise LoginRequired()
    return recipient


CurrentRecipient = Annotated[Recipient, Depends(require_recipient)]

router = APIRouter()


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def blank_to_none(raw: str) -> str | None:
    stripped = raw.strip()
    return stripped if stripped else None


@router.get("/")
def index(request: Request, templates: Templates):
    return templates.TemplateResponse(
        request, "index.html", {"csrf": csrf_token(request)}
    )


@router.post("/signup")
def signup(
    request: Request,
    db: Db,
    mailer: Mail,
    csrf: Annotated[str, Form()],
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    address_line1: Annotated[str, Form()],
    city: Annotated[str, Form()],
    postal_code: Annotated[str, Form()],
    country: Annotated[str, Form()],
    address_line2: Annotated[str, Form()] = "",
    region: Annotated[str, Form()] = "",
):
    require_csrf(request, csrf)
    address = normalize_email(email)
    existing = db.scalar(select(Recipient).where(Recipient.email == address))
    if existing is None:
        recipient = Recipient(
            email=address,
            name=name.strip(),
            address_line1=address_line1.strip(),
            address_line2=blank_to_none(address_line2),
            city=city.strip(),
            region=blank_to_none(region),
            postal_code=postal_code.strip(),
            country=country.strip(),
            verified_at=None,
            unsubscribed_at=None,
        )
        db.add(recipient)
        db.flush()
        record_version(db, recipient, utcnow())
    send_login_code(db, mailer, address)
    return RedirectResponse(f"/verify?email={address}", status_code=303)


@router.get("/login")
def login_form(request: Request, templates: Templates):
    return templates.TemplateResponse(
        request, "login.html", {"csrf": csrf_token(request)}
    )


@router.post("/login")
def login(
    request: Request,
    db: Db,
    mailer: Mail,
    csrf: Annotated[str, Form()],
    email: Annotated[str, Form()],
):
    require_csrf(request, csrf)
    address = normalize_email(email)
    existing = db.scalar(select(Recipient).where(Recipient.email == address))
    if existing is not None:
        send_login_code(db, mailer, address)
    return RedirectResponse(f"/verify?email={address}", status_code=303)


@router.get("/verify")
def verify_form(request: Request, templates: Templates, email: str):
    return templates.TemplateResponse(
        request,
        "verify.html",
        {"csrf": csrf_token(request), "email": normalize_email(email), "error": None},
    )


@router.post("/verify")
def verify(
    request: Request,
    db: Db,
    templates: Templates,
    csrf: Annotated[str, Form()],
    email: Annotated[str, Form()],
    code: Annotated[str, Form()],
):
    require_csrf(request, csrf)
    address = normalize_email(email)
    if not consume_login_code(db, address, code.strip(), utcnow()):
        return templates.TemplateResponse(
            request,
            "verify.html",
            {
                "csrf": csrf_token(request),
                "email": address,
                "error": "That code didn't work. Check it, or request a new one.",
            },
            status_code=422,
        )
    recipient = db.scalar(select(Recipient).where(Recipient.email == address))
    if recipient is None:
        raise LoginRequired()
    if recipient.verified_at is None:
        recipient.verified_at = utcnow()
    request.session["recipient_id"] = recipient.id
    return RedirectResponse("/account", status_code=303)


@router.get("/account")
def account(request: Request, templates: Templates, recipient: CurrentRecipient):
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "csrf": csrf_token(request),
            "recipient": recipient,
            "saved": "saved" in request.query_params,
        },
    )


@router.post("/account")
def update_account(
    request: Request,
    db: Db,
    recipient: CurrentRecipient,
    csrf: Annotated[str, Form()],
    name: Annotated[str, Form()],
    address_line1: Annotated[str, Form()],
    city: Annotated[str, Form()],
    postal_code: Annotated[str, Form()],
    country: Annotated[str, Form()],
    address_line2: Annotated[str, Form()] = "",
    region: Annotated[str, Form()] = "",
):
    require_csrf(request, csrf)
    recipient.name = name.strip()
    recipient.address_line1 = address_line1.strip()
    recipient.address_line2 = blank_to_none(address_line2)
    recipient.city = city.strip()
    recipient.region = blank_to_none(region)
    recipient.postal_code = postal_code.strip()
    recipient.country = country.strip()
    db.add(recipient)
    record_version(db, recipient, utcnow())
    return RedirectResponse("/account?saved=1", status_code=303)


@router.post("/account/unregister")
def unregister(
    request: Request, db: Db, recipient: CurrentRecipient, csrf: Annotated[str, Form()]
):
    require_csrf(request, csrf)
    recipient.unsubscribed_at = utcnow()
    db.add(recipient)
    request.session.pop("recipient_id", None)
    return RedirectResponse("/goodbye", status_code=303)


@router.post("/account/reregister")
def reregister(
    request: Request, db: Db, recipient: CurrentRecipient, csrf: Annotated[str, Form()]
):
    require_csrf(request, csrf)
    recipient.unsubscribed_at = None
    db.add(recipient)
    return RedirectResponse("/account", status_code=303)


@router.get("/about")
def about(request: Request, templates: Templates):
    return templates.TemplateResponse(request, "about.html", {})


@router.get("/privacy")
def privacy(request: Request, templates: Templates):
    return templates.TemplateResponse(request, "privacy.html", {})


@router.get("/goodbye")
def goodbye(request: Request, templates: Templates):
    return templates.TemplateResponse(request, "goodbye.html", {})


@router.post("/logout")
def logout(request: Request, csrf: Annotated[str, Form()]):
    require_csrf(request, csrf)
    request.session.pop("recipient_id", None)
    return RedirectResponse("/", status_code=303)
