"""Database models. SQLite dialect only."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid6 import uuid7

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
)


def utcnow() -> datetime:
    """Naive UTC timestamp — SQLite has no timezone type, so we store naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    """UUIDv7 primary key: millisecond timestamp prefix, random tail."""
    return str(uuid7())


class Base(DeclarativeBase):
    pass


class UserStatus(StrEnum):
    """Subscription lifecycle: signed up, receiving mail, or unregistered."""

    PENDING = "pending"
    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"


class EditionStatus(StrEnum):
    """An edition accepts new mailpieces while open."""

    OPEN = "open"
    CLOSED = "closed"


def status_enum(values: type[StrEnum]) -> Enum:
    """Store the enum's lowercase values, not its member names."""
    return Enum(values, values_callable=lambda e: [m.value for m in e])


class User(Base):
    """A person who signed up: an email identity plus the state of their
    subscription to future editions."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    status: Mapped[UserStatus] = mapped_column(
        status_enum(UserStatus), default=UserStatus.PENDING
    )
    verified_at: Mapped[datetime | None]
    unsubscribed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    addresses: Mapped[list["Address"]] = relationship(back_populates="user")
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="user")


class UserSession(Base):
    """One authenticated browser session for a user."""

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(43), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class Address(Base):
    """One postal address a user has had. Append-only: an edit inserts a new row.
    derived_from_id links a validated address to the manual entry it corrects."""

    __tablename__ = "addresses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    addressee: Mapped[str] = mapped_column(String(200))
    address_line1: Mapped[str] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(120))
    derived_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("addresses.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="addresses")


class Edition(Base):
    """One print run mailed to many users — a postcard design, a photo, a letter.
    Each physical copy sent is a Mailpiece."""

    __tablename__ = "editions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[EditionStatus] = mapped_column(
        status_enum(EditionStatus), default=EditionStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="edition")


class Mailpiece(Base):
    """One physical piece of mail: an edition sent to one user, at the exact
    address written on it."""

    __tablename__ = "mailpieces"
    __table_args__ = (UniqueConstraint("edition_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    edition_id: Mapped[str] = mapped_column(ForeignKey("editions.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    address_id: Mapped[str] = mapped_column(ForeignKey("addresses.id"))
    sent_at: Mapped[datetime] = mapped_column(default=utcnow)

    edition: Mapped[Edition] = relationship(back_populates="mailpieces")
    user: Mapped[User] = relationship(back_populates="mailpieces")
    address: Mapped[Address] = relationship()


class LoginCode(Base):
    __tablename__ = "login_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)


class AdminSession(Base):
    """One signed-in admin browser session."""

    __tablename__ = "admin_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


ADDRESS_FIELDS = (
    "addressee",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
)


def latest_manual_address(db: Session, user_id: str) -> Address | None:
    """The user's newest self-entered address — what the account page shows."""
    return db.scalar(
        select(Address)
        .where(Address.user_id == user_id, Address.derived_from_id.is_(None))
        .order_by(Address.created_at.desc(), Address.id.desc())
        .limit(1)
    )


def mailable_address(db: Session, user_id: str) -> Address | None:
    """The address to put on an envelope: the validated derivative of the latest
    manual entry when one exists, else the manual entry itself."""
    manual = latest_manual_address(db, user_id)
    if manual is None:
        return None
    validated = db.scalar(
        select(Address)
        .where(Address.derived_from_id == manual.id)
        .order_by(Address.created_at.desc(), Address.id.desc())
        .limit(1)
    )
    return validated or manual


def record_address(
    db: Session, user_id: str, values: dict[str, str | None], now: datetime
) -> None:
    """Append a manual address unless the latest manual address is identical."""
    current = latest_manual_address(db, user_id)
    if current is not None and all(
        getattr(current, field) == value for field, value in values.items()
    ):
        return
    db.add(Address(user_id=user_id, created_at=now, **values))


def activate(user: User, now: datetime) -> None:
    """Move a pending user to active, recording when their email was verified."""
    user.status = UserStatus.ACTIVE
    user.verified_at = now


def unsubscribe(user: User, now: datetime) -> None:
    """End the user's subscription: no future editions are mailed to them."""
    user.status = UserStatus.UNSUBSCRIBED
    user.unsubscribed_at = now


def resubscribe(user: User) -> None:
    """Restart an unsubscribed user's subscription."""
    user.status = UserStatus.ACTIVE
    user.unsubscribed_at = None
