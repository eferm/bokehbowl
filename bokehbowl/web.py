"""Public routes: signup, sign-in via email code, and the account page."""

import secrets
from collections.abc import Iterator
from datetime import timedelta
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
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from bokehbowl.auth import (
    consume_login_code,
    require_csrf,
    send_login_code,
    volume_capped,
)
from bokehbowl.db import Recipient, RecipientSession, record_version, utcnow
from bokehbowl.mailer import Mailer

RECIPIENT_SESSION_TTL = timedelta(days=30)


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
    recipient_token = request.session.get("recipient_token")
    if not isinstance(recipient_token, str):
        raise LoginRequired()
    session = db.get(RecipientSession, recipient_token)
    if session is None:
        raise LoginRequired()
    if session.created_at < utcnow() - RECIPIENT_SESSION_TTL:
        raise LoginRequired()
    return session.recipient


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
    return templates.TemplateResponse(request, "index.html", {"error": None})


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
    return templates.TemplateResponse(request, "login.html", {"error": None})


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
        {"email": normalize_email(email), "error": None},
    )


@router.post("/verify")
def verify(
    request: Request,
    db: Db,
    templates: Templates,
    mailer: Mail,
    background: BackgroundTasks,
    form: Annotated[VerifyForm, Form()],
):
    address = form.email
    now = utcnow()
    if not consume_login_code(db, address, form.code, now):
        return templates.TemplateResponse(
            request,
            "verify.html",
            {
                "email": address,
                "error": "That code didn't work. Check it, or request a new one.",
            },
            status_code=422,
        )
    recipient = db.scalar(select(Recipient).where(Recipient.email == address))
    if recipient is None:
        raise LoginRequired()
    newly_verified = recipient.verified_at is None
    if newly_verified:
        recipient.verified_at = now
        background.add_task(
            mailer.send,
            to=request.app.state.config.notify_email,
            subject=f"New signup: {recipient.name}",
            body=f"{recipient.name} <{recipient.email}> signed up.",
        )
    db.execute(
        delete(RecipientSession).where(
            RecipientSession.created_at < now - RECIPIENT_SESSION_TTL
        )
    )
    session = RecipientSession(
        recipient_id=recipient.id,
        token=secrets.token_urlsafe(32),
    )
    db.add(session)
    request.session["recipient_token"] = session.token
    db.commit()
    destination = "/account?created=1" if newly_verified else "/account"
    return RedirectResponse(destination, status_code=303)


@router.get("/account")
def account(request: Request, templates: Templates, recipient: CurrentRecipient):
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "recipient": recipient,
            "created": request.query_params.get("created") == "1",
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
    db.add(recipient)
    db.execute(
        delete(RecipientSession).where(RecipientSession.recipient_id == recipient.id)
    )
    request.session.pop("recipient_token", None)
    return RedirectResponse("/goodbye", status_code=303)


@router.post("/account/reregister")
def reregister(request: Request, db: Db, recipient: CurrentRecipient):
    recipient.unsubscribed_at = None
    db.add(recipient)
    return RedirectResponse("/account", status_code=303)


@router.get("/privacy")
def privacy(request: Request, templates: Templates):
    return templates.TemplateResponse(request, "privacy.html", {})


@router.get("/goodbye")
def goodbye(request: Request, templates: Templates):
    return templates.TemplateResponse(request, "goodbye.html", {})


@router.post("/logout")
def logout(request: Request, db: Db):
    recipient_token = request.session.get("recipient_token")
    if isinstance(recipient_token, str):
        db.execute(
            delete(RecipientSession).where(RecipientSession.token == recipient_token)
        )
    request.session.pop("recipient_token", None)
    return RedirectResponse("/", status_code=303)
