"""Database models. SQLite dialect only."""

from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from uuid6 import uuid7

from sqlalchemy import (
    Engine,
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
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

    addresses: Mapped[list["Address"]] = relationship(back_populates="user")
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="user")


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

    address: Mapped[Address] = relationship()


class Edition(Base):
    """One print run mailed to many users — a postcard design, a photo, a letter.
    Each physical copy sent is a Mailpiece."""

    __tablename__ = "editions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    mailpieces: Mapped[list["Mailpiece"]] = relationship(back_populates="edition")


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


def latest_address(db: Session, user_id: str) -> Address:
    """The user's latest address, created_at ties broken on id. Every user has
    one: registration records it."""
    return db.scalars(
        select(Address)
        .where(Address.user_id == user_id)
        .order_by(Address.created_at.desc(), Address.id.desc())
        .limit(1)
    ).one()


def latest_normalized_address(
    db: Session, address: Address
) -> NormalizedAddress | None:
    """The address's current print version, once the operator has filed one.
    An address with a print version is ready to send; envelopes print it."""
    return db.scalars(
        select(NormalizedAddress)
        .where(NormalizedAddress.address_id == address.id)
        .order_by(NormalizedAddress.created_at.desc(), NormalizedAddress.id.desc())
        .limit(1)
    ).first()


def record_address(db: Session, user_id: str, submitted: AddressComponents) -> None:
    """Append an address unless the user's latest address is identical."""
    if submitted == latest_address(db, user_id).components:
        return
    db.add(Address(user_id=user_id, **asdict(submitted)))


def record_normalized_address(
    db: Session, address: Address, submitted: AddressComponents
) -> None:
    """Append a print version of the address unless its latest print version is
    identical."""
    current = latest_normalized_address(db, address)
    if current is not None and submitted == current.components:
        return
    db.add(NormalizedAddress(address_id=address.id, **asdict(submitted)))


def register_user(db: Session, email: str, submitted: AddressComponents) -> User:
    """Create a user and their first address, in one transaction."""
    user = User(email=email)
    db.add(user)
    db.flush()
    db.add(Address(user_id=user.id, **asdict(submitted)))
    return user


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
