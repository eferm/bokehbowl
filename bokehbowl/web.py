"""Public routes: signup, sign-in via email code, and the account page."""

import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
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
    StringConstraints,
    field_validator,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from bokehbowl.auth import (
    consume_login_code,
    require_csrf,
    send_login_code,
)
from bokehbowl.db import (
    AddressComponents,
    User,
    UserSession,
    current_address,
    record_address,
    register_user,
    utcnow,
)
from bokehbowl.mailer import Mailer


USER_SESSION_TTL = timedelta(days=30)
COUNTRIES = tuple(
    (Path(__file__).parent / "resources" / "countries.txt")
    .read_text(encoding="utf-8")
    .splitlines()
)


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


def live_session(db: Session, request: Request) -> UserSession | None:
    """The browser's session, while its token names a row younger than
    USER_SESSION_TTL. The one definition of a signed-in browser; the pages that
    need a user and the nav that offers to sign one out both ask it."""
    user_token = request.session.get("user_token")
    if not isinstance(user_token, str):
        return None
    session = db.get(UserSession, user_token)
    if session is None or session.created_at < utcnow() - USER_SESSION_TTL:
        return None
    return session


def require_user(request: Request, db: Db) -> User:
    session = live_session(db, request)
    if session is None:
        request.session.pop("user_token", None)
        raise LoginRequired()
    return session.user


CurrentUser = Annotated[User, Depends(require_user)]

router = APIRouter(dependencies=[Depends(require_csrf)])


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


NormalizedEmail = Annotated[EmailStr, BeforeValidator(normalize_email)]
VerificationCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=6,
        max_length=6,
        pattern=r"^[0-9]{6}$",
    ),
]


class AddressForm(BaseModel):
    """A user's name and postal address: one line of printable text per field,
    with runs of whitespace folded to single spaces."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    address_line1: str = Field(min_length=1, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(min_length=1, max_length=120)

    @field_validator(
        "name",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country",
        mode="before",
    )
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is None:
            return None
        folded = " ".join(value.split())
        if not folded.isprintable():
            raise ValueError("holds a control character")
        return folded

    @field_validator("address_line2", "region")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("country")
    @classmethod
    def country_name(cls, value: str) -> str:
        if value not in COUNTRIES:
            raise ValueError("is not a supported mailing country name")
        return value

    @property
    def components(self) -> AddressComponents:
        """The submitted address, as a value."""
        return AddressComponents(
            addressee=self.name,
            address_line1=self.address_line1,
            address_line2=self.address_line2,
            city=self.city,
            region=self.region,
            postal_code=self.postal_code,
            country=self.country,
        )


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
    code: VerificationCode


class SignupVerifyForm(SignupForm):
    """A signup code submission: the signup payload plus the code sent to its
    email."""

    code: VerificationCode


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
    if live_session(db, request) is not None:
        return RedirectResponse("/account", status_code=303)
    send_login_code(db, mailer, form.email, background)
    return templates.TemplateResponse(
        request,
        "verify.html",
        {"email": form.email, "error": None, "signup": form, "code_outstanding": True},
    )


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
    """Emails a sign-in code to an address that has an account, then sends every
    caller to the code form."""
    existing = db.scalar(select(User).where(User.email == form.email))
    if existing is not None:
        send_login_code(db, mailer, form.email, background)
    return RedirectResponse(f"/login/verify?email={form.email}", status_code=303)


@router.get("/login/verify")
def login_verify_form(
    request: Request, templates: Templates, email: NormalizedEmail
):
    return templates.TemplateResponse(
        request,
        "verify.html",
        {
            "email": email,
            "error": None,
            "signup": None,
            "code_outstanding": True,
        },
    )


@router.get("/signup/verify")
def signup_verify_form():
    """The signup code form lives in the response to POST /signup, which carries
    the signup payload; a direct visit starts at the signup form."""
    return RedirectResponse("/", status_code=303)


def start_session(
    request: Request, db: Session, user: User, now: datetime, *, destination: str
) -> RedirectResponse:
    """A redirect to the destination carrying a fresh session: expired sessions
    pruned, a new one minted and bound to the browser."""
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
    return RedirectResponse(destination, status_code=303)


@router.post("/signup/verify")
def signup_verify(
    request: Request,
    db: Db,
    templates: Templates,
    mailer: Mail,
    background: BackgroundTasks,
    form: Annotated[SignupVerifyForm, Form()],
):
    now = utcnow()
    if not consume_login_code(db, form.email, form.code, now):
        return templates.TemplateResponse(
            request,
            "verify.html",
            {
                "email": form.email,
                "error": "That code didn't work. Check it, or request a new one.",
                "signup": form,
                "code_outstanding": True,
            },
            status_code=422,
        )
    user = db.scalar(select(User).where(User.email == form.email))
    if user is not None:
        return start_session(request, db, user, now, destination="/account?existing=1")
    user = register_user(db, form.email, form.components)
    background.add_task(
        mailer.send,
        to=request.app.state.config.notify_email,
        subject=f"New signup: {form.name}",
        body=f"{form.name} <{user.email}> signed up.",
    )
    return start_session(request, db, user, now, destination="/account?created=1")


@router.post("/login/verify")
def login_verify(
    request: Request,
    db: Db,
    templates: Templates,
    form: Annotated[VerifyForm, Form()],
):
    now = utcnow()
    if not consume_login_code(db, form.email, form.code, now):
        return templates.TemplateResponse(
            request,
            "verify.html",
            {
                "email": form.email,
                "error": "That code didn't work. Check it, or request a new one.",
                "signup": None,
                "code_outstanding": True,
            },
            status_code=422,
        )
    user = db.scalar(select(User).where(User.email == form.email))
    if user is None:
        raise LoginRequired()
    return start_session(request, db, user, now, destination="/account")


@router.get("/account")
def account(request: Request, db: Db, templates: Templates, user: CurrentUser):
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "user": user,
            "address": current_address(db, user),
            "created": request.query_params.get("created") == "1",
            "existing": request.query_params.get("existing") == "1",
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
    record_address(db, user, form.components)
    return RedirectResponse("/account?saved=1", status_code=303)


@router.post("/account/unsubscribe")
def unsubscribe(user: CurrentUser):
    user.unsubscribed_at = utcnow()
    return RedirectResponse("/account", status_code=303)


@router.post("/account/resubscribe")
def resubscribe(request: Request, db: Db, user: CurrentUser):
    user.unsubscribed_at = None
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
