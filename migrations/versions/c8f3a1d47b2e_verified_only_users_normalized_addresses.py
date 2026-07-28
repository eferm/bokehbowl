"""verified-only users; normalized addresses

Users exist only after verification: unverified user rows and their address
rows are removed. Address rows with derived_from_id become normalized_addresses
rows keyed to their parent. A
mailpiece pins the print version its envelope carried: one that pointed at
a derived row takes it as normalized_address_id; one that pointed at a manual
row gets a normalized_addresses copy of that row, filed at the send moment.
verified_at and derived_from_id are dropped; user-rooted foreign keys
cascade on delete.

Both directions read every row into memory first, swap the schema, then load
the mapped rows back.

Revision ID: c8f3a1d47b2e
Revises: 9157d1772fb8
Create Date: 2026-07-28 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from uuid6 import uuid7

revision = "c8f3a1d47b2e"
down_revision = "9157d1772fb8"
branch_labels = None
depends_on = None


def _create_new_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)

    op.create_table(
        "addresses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("addressee", sa.String(length=200), nullable=False),
        sa.Column("address_line1", sa.String(length=200), nullable=False),
        sa.Column("address_line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("addresses", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_addresses_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "normalized_addresses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("address_id", sa.String(length=36), nullable=False),
        sa.Column("addressee", sa.String(length=200), nullable=False),
        sa.Column("address_line1", sa.String(length=200), nullable=False),
        sa.Column("address_line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["address_id"], ["addresses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("normalized_addresses", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_normalized_addresses_address_id"), ["address_id"], unique=False
        )

    op.create_table(
        "user_sessions",
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
    )

    op.create_table(
        "mailpieces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("edition_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("normalized_address_id", sa.String(length=36), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["normalized_address_id"], ["normalized_addresses.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["edition_id"], ["editions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("edition_id", "user_id"),
    )
    with op.batch_alter_table("mailpieces", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_mailpieces_edition_id"), ["edition_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mailpieces_user_id"), ["user_id"], unique=False
        )


def _create_old_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_email"), ["email"], unique=True)

    op.create_table(
        "addresses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("addressee", sa.String(length=200), nullable=False),
        sa.Column("address_line1", sa.String(length=200), nullable=False),
        sa.Column("address_line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("derived_from_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["derived_from_id"], ["addresses.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("addresses", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_addresses_user_id"), ["user_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_addresses_derived_from_id"),
            ["derived_from_id"],
            unique=False,
        )

    op.create_table(
        "user_sessions",
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token"),
    )

    op.create_table(
        "mailpieces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("edition_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("address_id", sa.String(length=36), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["address_id"], ["addresses.id"]),
        sa.ForeignKeyConstraint(["edition_id"], ["editions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("edition_id", "user_id"),
    )
    with op.batch_alter_table("mailpieces", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_mailpieces_edition_id"), ["edition_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mailpieces_user_id"), ["user_id"], unique=False
        )


ADDRESS_FIELDS = (
    "addressee",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
)


def _read_old_rows(bind) -> dict[str, list[dict]]:
    """The old schema's rows, mapped onto the new schema's shape."""
    old_users = list(
        bind.execute(
            sa.text(
                "SELECT id, email, verified_at, unsubscribed_at, created_at"
                " FROM users"
            )
        )
    )
    old_addresses = list(
        bind.execute(
            sa.text(
                "SELECT id, user_id, addressee, address_line1, address_line2,"
                " city, region, postal_code, country, derived_from_id, created_at"
                " FROM addresses ORDER BY created_at, id"
            )
        )
    )
    unverified_ids = {u.id for u in old_users if u.verified_at is None}

    users = [
        {
            "id": u.id,
            "email": u.email,
            "unsubscribed_at": u.unsubscribed_at,
            "created_at": u.created_at,
        }
        for u in old_users
        if u.id not in unverified_ids
    ]

    addresses = []
    normalized_addresses = []
    derived_to_parent: dict[str, str] = {}
    for a in old_addresses:
        if a.derived_from_id is not None:
            derived_to_parent[a.id] = a.derived_from_id
            if a.user_id not in unverified_ids:
                normalized_addresses.append(
                    {
                        "id": a.id,
                        "address_id": a.derived_from_id,
                        **{field: getattr(a, field) for field in ADDRESS_FIELDS},
                        "created_at": a.created_at,
                    }
                )
            continue
        if a.user_id not in unverified_ids:
            addresses.append(
                {
                    "id": a.id,
                    "user_id": a.user_id,
                    **{field: getattr(a, field) for field in ADDRESS_FIELDS},
                    "created_at": a.created_at,
                }
            )

    user_sessions = [
        {"token": s.token, "user_id": s.user_id, "created_at": s.created_at}
        for s in bind.execute(
            sa.text("SELECT token, user_id, created_at FROM user_sessions")
        )
        if s.user_id not in unverified_ids
    ]
    address_by_id = {a.id: a for a in old_addresses}
    printed_copy_of: dict[str, str] = {}
    mailpieces = []
    for m in bind.execute(
        sa.text("SELECT id, edition_id, user_id, address_id, sent_at FROM mailpieces")
    ):
        if m.address_id in derived_to_parent:
            normalized_id = m.address_id
        elif m.address_id in printed_copy_of:
            normalized_id = printed_copy_of[m.address_id]
        else:
            normalized_id = str(uuid7())
            printed_copy_of[m.address_id] = normalized_id
            printed = address_by_id[m.address_id]
            normalized_addresses.append(
                {
                    "id": normalized_id,
                    "address_id": m.address_id,
                    **{field: getattr(printed, field) for field in ADDRESS_FIELDS},
                    "created_at": m.sent_at,
                }
            )
        mailpieces.append(
            {
                "id": m.id,
                "edition_id": m.edition_id,
                "user_id": m.user_id,
                "normalized_address_id": normalized_id,
                "sent_at": m.sent_at,
            }
        )
    return {
        "users": users,
        "addresses": addresses,
        "normalized_addresses": normalized_addresses,
        "user_sessions": user_sessions,
        "mailpieces": mailpieces,
    }


def _read_new_rows(bind) -> dict[str, list[dict]]:
    """The new schema's rows, mapped onto the old schema's shape."""
    users = [
        {
            "id": u.id,
            "email": u.email,
            "verified_at": u.created_at,
            "unsubscribed_at": u.unsubscribed_at,
            "created_at": u.created_at,
        }
        for u in bind.execute(
            sa.text("SELECT id, email, unsubscribed_at, created_at FROM users")
        )
    ]
    addresses = []
    user_of_address: dict[str, str] = {}
    fields_of_address: dict[str, tuple] = {}
    for a in bind.execute(
        sa.text(
            "SELECT id, user_id, addressee, address_line1, address_line2,"
            " city, region, postal_code, country, created_at FROM addresses"
        )
    ):
        user_of_address[a.id] = a.user_id
        fields_of_address[a.id] = tuple(getattr(a, field) for field in ADDRESS_FIELDS)
        addresses.append(
            {
                "id": a.id,
                "user_id": a.user_id,
                **{field: getattr(a, field) for field in ADDRESS_FIELDS},
                "derived_from_id": None,
                "created_at": a.created_at,
            }
        )
    # A print version whose fields match its address prints the same envelope.
    # The old schema says that by pointing the mailpiece at the address itself,
    # so those rows collapse into their parent rather than becoming copies.
    verbatim_prints: dict[str, str] = {}
    for c in bind.execute(
        sa.text(
            "SELECT id, address_id, addressee, address_line1, address_line2,"
            " city, region, postal_code, country, created_at FROM normalized_addresses"
        )
    ):
        fields = tuple(getattr(c, field) for field in ADDRESS_FIELDS)
        if fields == fields_of_address[c.address_id]:
            verbatim_prints[c.id] = c.address_id
            continue
        addresses.append(
            {
                "id": c.id,
                "user_id": user_of_address[c.address_id],
                **{field: getattr(c, field) for field in ADDRESS_FIELDS},
                "derived_from_id": c.address_id,
                "created_at": c.created_at,
            }
        )
    user_sessions = [
        {"token": s.token, "user_id": s.user_id, "created_at": s.created_at}
        for s in bind.execute(
            sa.text("SELECT token, user_id, created_at FROM user_sessions")
        )
    ]
    mailpieces = [
        {
            "id": m.id,
            "edition_id": m.edition_id,
            "user_id": m.user_id,
            "address_id": verbatim_prints.get(
                m.normalized_address_id, m.normalized_address_id
            ),
            "sent_at": m.sent_at,
        }
        for m in bind.execute(
            sa.text(
                "SELECT id, edition_id, user_id, normalized_address_id, sent_at"
                " FROM mailpieces"
            )
        )
    ]
    return {
        "users": users,
        "addresses": addresses,
        "user_sessions": user_sessions,
        "mailpieces": mailpieces,
    }


def _insert_rows(bind, table: str, rows: list[dict]) -> None:
    if rows:
        columns = ", ".join(rows[0])
        placeholders = ", ".join(f":{column}" for column in rows[0])
        bind.execute(
            sa.text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), rows
        )


def upgrade() -> None:
    bind = op.get_bind()
    snapshot = _read_old_rows(bind)
    for table in ("mailpieces", "user_sessions", "addresses", "users"):
        op.drop_table(table)
    _create_new_tables()
    for table in (
        "users",
        "addresses",
        "normalized_addresses",
        "user_sessions",
        "mailpieces",
    ):
        _insert_rows(bind, table, snapshot[table])


def downgrade() -> None:
    bind = op.get_bind()
    snapshot = _read_new_rows(bind)
    for table in (
        "mailpieces",
        "user_sessions",
        "normalized_addresses",
        "addresses",
        "users",
    ):
        op.drop_table(table)
    _create_old_tables()
    for table in ("users", "addresses", "user_sessions", "mailpieces"):
        _insert_rows(bind, table, snapshot[table])
