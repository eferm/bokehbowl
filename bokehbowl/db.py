"""Database models. SQLite dialect only, so the schema stays portable to D1."""

from datetime import UTC, datetime
from uuid6 import uuid7

from sqlalchemy import ForeignKey, String, UniqueConstraint, select
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


class Recipient(Base):
    """Current state of a person. One row per person; history lives in
    recipient_versions."""

    __tablename__ = "recipients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    address_line1: Mapped[str] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    verified_at: Mapped[datetime | None]
    unsubscribed_at: Mapped[datetime | None]
    versions: Mapped[list["RecipientVersion"]] = relationship(
        back_populates="recipient"
    )
    sessions: Mapped[list["RecipientSession"]] = relationship(
        back_populates="recipient", cascade="all, delete-orphan"
    )
    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="recipient")


class RecipientSession(Base):
    """One authenticated browser session for a recipient."""

    __tablename__ = "recipient_sessions"

    token: Mapped[str] = mapped_column(String(43), primary_key=True)
    recipient_id: Mapped[str] = mapped_column(ForeignKey("recipients.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    recipient: Mapped[Recipient] = relationship(back_populates="sessions")


class RecipientVersion(Base):
    """Append-only snapshot of a recipient's identity fields. One row per state a
    recipient has ever been in; valid until the next version's valid_from."""

    __tablename__ = "recipient_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    recipient_id: Mapped[str] = mapped_column(ForeignKey("recipients.id"), index=True)
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str] = mapped_column(String(200))
    address_line1: Mapped[str] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(120))
    valid_from: Mapped[datetime]

    recipient: Mapped[Recipient] = relationship(back_populates="versions")


class Mailing(Base):
    """One specific thing mailed to many recipients — a postcard design or print
    run, a photo, a letter. Each physical copy sent is a Mailpiece. The frontend
    calls it a picture; the capability is generic."""

    __tablename__ = "mailings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="mailing")


class Mailpiece(Base):
    """One physical piece of mail (USPS's word): a mailing sent to one recipient,
    at the exact address version written on it."""

    __tablename__ = "mailpieces"
    __table_args__ = (UniqueConstraint("mailing_id", "recipient_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mailing_id: Mapped[str] = mapped_column(ForeignKey("mailings.id"), index=True)
    recipient_id: Mapped[str] = mapped_column(ForeignKey("recipients.id"), index=True)
    recipient_version_id: Mapped[str] = mapped_column(
        ForeignKey("recipient_versions.id")
    )
    sent_at: Mapped[datetime] = mapped_column(default=utcnow)

    mailing: Mapped[Mailing] = relationship(back_populates="mailpieces")
    recipient: Mapped[Recipient] = relationship(back_populates="mailpieces")
    recipient_version: Mapped[RecipientVersion] = relationship()


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


SNAPSHOT_FIELDS = (
    "email",
    "name",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
)


def latest_version(db: Session, recipient_id: str) -> RecipientVersion | None:
    return db.scalar(
        select(RecipientVersion)
        .where(RecipientVersion.recipient_id == recipient_id)
        .order_by(RecipientVersion.valid_from.desc())
        .limit(1)
    )


def record_version(db: Session, recipient: Recipient, now: datetime) -> None:
    """Append a version snapshot unless the latest version is already identical."""
    snapshot = {field: getattr(recipient, field) for field in SNAPSHOT_FIELDS}
    current = latest_version(db, recipient.id)
    if current is not None and all(
        getattr(current, field) == value for field, value in snapshot.items()
    ):
        return
    db.add(RecipientVersion(recipient_id=recipient.id, valid_from=now, **snapshot))
