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
from bokehbowl.db import (
    User,
    UserSession,
    UserStatus,
    activate,
    latest_manual_address,
    record_address,
    resubscribe,
    unsubscribe,
    utcnow,
)
from bokehbowl.mailer import Mailer


USER_SESSION_TTL = timedelta(days=30)


class LoginRequired(Exception):
    """Raised when a page needs a signed-in user and the session has none."""


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


def require_user(request: Request, db: Db) -> User:
    user_token = request.session.get("user_token")
    if not isinstance(user_token, str):
        raise LoginRequired()
    session = db.get(UserSession, user_token)
    if session is None:
        raise LoginRequired()
    if session.created_at < utcnow() - USER_SESSION_TTL:
        raise LoginRequired()
    return session.user


CurrentUser = Annotated[User, Depends(require_user)]

router = APIRouter(dependencies=[Depends(require_csrf)])


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


NormalizedEmail = Annotated[EmailStr, BeforeValidator(normalize_email)]


class AddressForm(BaseModel):
    """A user's name and postal address, whitespace-stripped on entry."""

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


def address_values(form: AddressForm) -> dict[str, str | None]:
    """Map a submitted address form onto address row fields."""
    return {
        "addressee": form.name,
        "address_line1": form.address_line1,
        "address_line2": form.address_line2,
        "city": form.city,
        "region": form.region,
        "postal_code": form.postal_code,
        "country": form.country,
    }


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
    existing = db.scalar(select(User).where(User.email == address))
    if existing is None:
        user = User(email=address, verified_at=None, unsubscribed_at=None)
        db.add(user)
        db.flush()
        record_address(db, user.id, address_values(form), utcnow())
    elif existing.status == UserStatus.PENDING:
        record_address(db, existing.id, address_values(form), utcnow())
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
    existing = db.scalar(select(User).where(User.email == address))
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
    user = db.scalar(select(User).where(User.email == address))
    if user is None:
        raise LoginRequired()
    newly_verified = user.status == UserStatus.PENDING
    if newly_verified:
        activate(user, now)
        manual = latest_manual_address(db, user.id)
        assert manual is not None  # signup always records an address
        background.add_task(
            mailer.send,
            to=request.app.state.config.notify_email,
            subject=f"New signup: {manual.addressee}",
            body=f"{manual.addressee} <{user.email}> signed up.",
        )
    db.execute(
        delete(UserSession).where(UserSession.created_at < now - USER_SESSION_TTL)
    )
    session = UserSession(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
    )
    db.add(session)
    request.session["user_token"] = session.token
    db.commit()
    destination = "/account?created=1" if newly_verified else "/account"
    return RedirectResponse(destination, status_code=303)


@router.get("/account")
def account(request: Request, db: Db, templates: Templates, user: CurrentUser):
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "user": user,
            "address": latest_manual_address(db, user.id),
            "created": request.query_params.get("created") == "1",
            "saved": "saved" in request.query_params,
            "editing": "edit" in request.query_params,
        },
    )


@router.post("/account")
def update_account(
    request: Request,
    db: Db,
    user: CurrentUser,
    form: Annotated[AddressForm, Form()],
):
    record_address(db, user.id, address_values(form), utcnow())
    return RedirectResponse("/account?saved=1", status_code=303)


@router.post("/account/unregister")
def unregister(request: Request, db: Db, user: CurrentUser):
    unsubscribe(user, utcnow())
    db.add(user)
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    request.session.pop("user_token", None)
    return RedirectResponse("/goodbye", status_code=303)


@router.post("/account/reregister")
def reregister(request: Request, db: Db, user: CurrentUser):
    resubscribe(user)
    db.add(user)
    return RedirectResponse("/account", status_code=303)


@router.get("/privacy")
def privacy(request: Request, templates: Templates):
    return templates.TemplateResponse(request, "privacy.html", {})


@router.get("/goodbye")
def goodbye(request: Request, templates: Templates):
    return templates.TemplateResponse(request, "goodbye.html", {})


@router.post("/logout")
def logout(request: Request, db: Db):
    user_token = request.session.get("user_token")
    if isinstance(user_token, str):
        db.execute(delete(UserSession).where(UserSession.token == user_token))
    request.session.pop("user_token", None)
    return RedirectResponse("/", status_code=303)
