"""Database models. SQLite dialect only."""

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Self
from uuid6 import uuid7

from sqlalchemy import (
    Engine,
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def utcnow() -> datetime:
    """Naive UTC timestamp — SQLite has no timezone type, so we store naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def new_id() -> str:
    """UUIDv7 primary key: millisecond timestamp prefix, random tail."""
    return str(uuid7())


class DerivedProperty(property):
    """A computed model field discoverable alongside stored database columns."""


def derived_property(method: Callable[..., object]) -> DerivedProperty:
    return DerivedProperty(method)


class Base(DeclarativeBase):
    @classmethod
    def derived_property_names(cls) -> tuple[str, ...]:
        """Names of computed fields exposed alongside stored columns."""
        columns: dict[str, DerivedProperty] = {}
        for base in reversed(cls.__mro__):
            for name, value in vars(base).items():
                columns.pop(name, None)
                if isinstance(value, DerivedProperty):
                    columns[name] = value
        return tuple(columns)


@dataclass(frozen=True)
class AddressComponents:
    """A postal address as a value: the parts an envelope prints. Two addresses
    with the same parts are equal."""

    addressee: str
    address_line1: str
    address_line2: str | None
    city: str
    region: str | None
    postal_code: str
    country: str


ADDRESS_FIELDS = tuple(field.name for field in fields(AddressComponents))
"""The address column names, in declaration order."""


class AddressMixin:
    """The components of a postal address. Each table that stores one inherits
    these columns, so the shape is identical everywhere."""

    addressee: Mapped[str] = mapped_column(String(200))
    address_line1: Mapped[str] = mapped_column(String(200))
    address_line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(120))

    @classmethod
    def from_components(cls, components: AddressComponents) -> Self:
        """Construct an address row from its shared component value."""
        return cls(**asdict(components))

    @property
    def components(self) -> AddressComponents:
        """The stored address, as a value."""
        return AddressComponents(
            addressee=self.addressee,
            address_line1=self.address_line1,
            address_line2=self.address_line2,
            city=self.city,
            region=self.region,
            postal_code=self.postal_code,
            country=self.country,
        )


class User(Base):
    """A verified email identity. created_at is the verification moment;
    unsubscribed_at set means mail stops."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    unsubscribed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    addresses: Mapped[list["Address"]] = relationship(
        back_populates="user",
        order_by=lambda: (Address.created_at, Address.id),
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="user")

    @classmethod
    def register(cls, email: str, submitted: AddressComponents) -> Self:
        """Create a user together with their required first address."""
        return cls(
            email=email,
            addresses=[Address.from_components(submitted)],
        )

    def record_address(self, submitted: AddressComponents) -> None:
        """Append an address unless the current one has the same components."""
        if submitted != self.current_address.components:
            self.addresses.append(Address.from_components(submitted))

    def unsubscribe(self, now: datetime) -> None:
        """Stop mail without replacing the original unsubscribe time."""
        if self.unsubscribed_at is None:
            self.unsubscribed_at = now

    def resubscribe(self) -> None:
        """Resume mail for this user."""
        self.unsubscribed_at = None

    @derived_property
    def current_address(self) -> "Address":
        """The latest address, with creation-time ties broken by id."""
        return self.addresses[-1]

    @derived_property
    def current_normalized_address(self) -> "NormalizedAddress | None":
        """The latest print version of the current address, if one exists."""
        return self.current_address.current_normalized_address


class UserSession(Base):
    """One authenticated browser session for a user."""

    __tablename__ = "user_sessions"

    token: Mapped[str] = mapped_column(String(43), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(back_populates="sessions")


class Address(AddressMixin, Base):
    """One postal address a user entered. Append-only: an edit inserts a new
    row; the latest row is current."""

    __tablename__ = "addresses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id, sort_order=-2
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, sort_order=-1
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, sort_order=1)

    user: Mapped[User] = relationship(back_populates="addresses")
    normalized_addresses: Mapped[list["NormalizedAddress"]] = relationship(
        back_populates="address",
        order_by=lambda: (NormalizedAddress.created_at, NormalizedAddress.id),
    )

    @property
    def current_normalized_address(self) -> "NormalizedAddress | None":
        """The latest print version filed for this address, if one exists."""
        return self.normalized_addresses[-1] if self.normalized_addresses else None

    def record_normalized_address(self, submitted: AddressComponents) -> None:
        """Append a print version unless the current one has the same components."""
        current = self.current_normalized_address
        if current is None or submitted != current.components:
            self.normalized_addresses.append(
                NormalizedAddress.from_components(submitted)
            )


class NormalizedAddress(AddressMixin, Base):
    """A print version of one address row, filed by the operator — approved as
    entered or edited. Append-only: envelopes print the latest normalized row
    for their address, and an address is sendable once it has one."""

    __tablename__ = "normalized_addresses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id, sort_order=-2
    )
    address_id: Mapped[str] = mapped_column(
        ForeignKey("addresses.id", ondelete="CASCADE"), index=True, sort_order=-1
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, sort_order=1)

    address: Mapped[Address] = relationship(back_populates="normalized_addresses")


class Edition(Base):
    """One print run mailed to many users — a postcard design, a photo, a letter.
    Each physical copy sent is a Mailpiece. deleted_at set archives the edition."""

    __tablename__ = "editions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]

    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="edition")

    @derived_property
    def sent_mailpieces(self) -> int:
        """The number of physical pieces recorded for this edition."""
        return len(self.mailpieces)

    def archive(self, now: datetime) -> None:
        """Soft-delete the edition without replacing its original archive time."""
        if self.deleted_at is None:
            self.deleted_at = now


class Mailpiece(Base):
    """One physical piece of mail: an edition sent to one user.
    normalized_address_id is the print version the envelope carried; the address
    of record at send time is that row's parent."""

    __tablename__ = "mailpieces"
    __table_args__ = (UniqueConstraint("edition_id", "user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    edition_id: Mapped[str] = mapped_column(ForeignKey("editions.id"), index=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    normalized_address_id: Mapped[str] = mapped_column(
        ForeignKey("normalized_addresses.id", ondelete="CASCADE")
    )
    sent_at: Mapped[datetime] = mapped_column(default=utcnow)

    edition: Mapped[Edition] = relationship(back_populates="mailpieces")
    user: Mapped[User] = relationship(back_populates="mailpieces")
    normalized_address: Mapped[NormalizedAddress] = relationship()

    @derived_property
    def mailing_group(self) -> str:
        """The edition group this recipient belongs to, derived from signup time."""
        return "base" if self.user.created_at <= self.edition.created_at else "late"


class LoginCode(Base):
    __tablename__ = "login_codes"

    TTL: ClassVar[timedelta] = timedelta(minutes=10)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(254), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]
    attempts: Mapped[int] = mapped_column(default=0)

    @staticmethod
    def hash(email: str, code: str) -> str:
        return hashlib.sha256(f"{email}:{code}".encode()).hexdigest()

    @classmethod
    def issue(cls, email: str, now: datetime) -> tuple[Self, str]:
        """Create a stored code and return it together with its plaintext value."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        return (
            cls(
                email=email,
                code_hash=cls.hash(email, code),
                created_at=now,
                expires_at=now + cls.TTL,
            ),
            code,
        )

    def matches(self, code: str) -> bool:
        return secrets.compare_digest(self.code_hash, self.hash(self.email, code))


class AdminSession(Base):
    """One signed-in admin browser session."""

    __tablename__ = "admin_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
    """Connect listener: turn SQLite foreign key enforcement on."""
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def build_engine(url: str, **kwargs: object) -> Engine:
    """Engine for the URL, with SQLite foreign key enforcement on every
    connection to a SQLite database."""
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listens_for(engine, "connect")(enable_foreign_keys)
    return engine
