"""Public routes: signup, sign-in via email code, and the account page."""

import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from bokehbowl.auth import (
    consume_login_code,
    csrf_token,
    require_csrf,
    send_login_code,
    volume_capped,
)
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
    if recipient.session_token is None or not secrets.compare_digest(
        recipient.session_token, request.session.get("recipient_token", "")
    ):
        raise LoginRequired()
    return recipient


CurrentRecipient = Annotated[Recipient, Depends(require_recipient)]

router = APIRouter(dependencies=[Depends(require_csrf)])


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


NormalizedEmail = Annotated[EmailStr, BeforeValidator(normalize_email)]


class AddressForm(BaseModel):
    """A recipient's name and postal address, whitespace-stripped on entry."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(max_length=200)
    address_line1: str = Field(max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str = Field(max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str = Field(max_length=20)
    country: str = Field(max_length=120)

    @field_validator("address_line2", "region")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        return value or None


class SignupForm(AddressForm):
    """A signup submission: an address plus the email to receive the code at."""

    email: NormalizedEmail


class LoginForm(BaseModel):
    """A login request naming the email to receive the code at."""

    email: NormalizedEmail


class VerifyForm(BaseModel):
    """A code submission: the email and the code sent to it."""

    model_config = ConfigDict(str_strip_whitespace=True)

    email: NormalizedEmail
    code: str


def apply_address(recipient: Recipient, form: AddressForm) -> None:
    """Copy the form's address fields onto the recipient."""
    recipient.name = form.name
    recipient.address_line1 = form.address_line1
    recipient.address_line2 = form.address_line2
    recipient.city = form.city
    recipient.region = form.region
    recipient.postal_code = form.postal_code
    recipient.country = form.country


@router.get("/")
def index(request: Request, templates: Templates):
    return templates.TemplateResponse(
        request, "index.html", {"csrf": csrf_token(request), "error": None}
    )


@router.post("/signup")
def signup(
    request: Request,
    db: Db,
    templates: Templates,
    mailer: Mail,
    background: BackgroundTasks,
    form: Annotated[SignupForm, Form()],
):
    if volume_capped(db, utcnow()):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "csrf": csrf_token(request),
                "error": (
                    "Sign-in codes are temporarily unavailable. Try again in an hour."
                ),
            },
            status_code=429,
        )
    address = form.email
    existing = db.scalar(select(Recipient).where(Recipient.email == address))
    if existing is None:
        recipient = Recipient(email=address, verified_at=None, unsubscribed_at=None)
        apply_address(recipient, form)
        db.add(recipient)
        db.flush()
        record_version(db, recipient, utcnow())
    elif existing.verified_at is None:
        apply_address(existing, form)
        db.add(existing)
        record_version(db, existing, utcnow())
    send_login_code(db, mailer, address, background)
    return RedirectResponse(f"/verify?email={address}", status_code=303)


@router.get("/login")
def login_form(request: Request, templates: Templates):
    return templates.TemplateResponse(
        request, "login.html", {"csrf": csrf_token(request), "error": None}
    )


@router.post("/login")
def login(
    request: Request,
    db: Db,
    templates: Templates,
    mailer: Mail,
    background: BackgroundTasks,
    form: Annotated[LoginForm, Form()],
):
    if volume_capped(db, utcnow()):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf": csrf_token(request),
                "error": (
                    "Sign-in codes are temporarily unavailable. Try again in an hour."
                ),
            },
            status_code=429,
        )
    address = form.email
    existing = db.scalar(select(Recipient).where(Recipient.email == address))
    if existing is not None:
        send_login_code(db, mailer, address, background)
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
    form: Annotated[VerifyForm, Form()],
):
    address = form.email
    if not consume_login_code(db, address, form.code, utcnow()):
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
    if recipient.session_token is None:
        recipient.session_token = secrets.token_urlsafe(32)
    request.session["recipient_id"] = recipient.id
    request.session["recipient_token"] = recipient.session_token
    db.commit()
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
    form: Annotated[AddressForm, Form()],
):
    apply_address(recipient, form)
    db.add(recipient)
    record_version(db, recipient, utcnow())
    return RedirectResponse("/account?saved=1", status_code=303)


@router.post("/account/unregister")
def unregister(request: Request, db: Db, recipient: CurrentRecipient):
    recipient.unsubscribed_at = utcnow()
    recipient.session_token = None
    db.add(recipient)
    request.session.pop("recipient_id", None)
    request.session.pop("recipient_token", None)
    return RedirectResponse("/goodbye", status_code=303)


@router.post("/account/reregister")
def reregister(request: Request, db: Db, recipient: CurrentRecipient):
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
def logout(request: Request, db: Db, recipient: CurrentRecipient):
    recipient.session_token = None
    db.add(recipient)
    request.session.pop("recipient_id", None)
    request.session.pop("recipient_token", None)
    return RedirectResponse("/", status_code=303)
