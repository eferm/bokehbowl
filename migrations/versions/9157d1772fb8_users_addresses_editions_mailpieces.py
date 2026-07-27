"""users, addresses, editions; mailpieces repointed to addresses

Recipients become users with an explicit subscription status. Recipient
versions become append-only addresses; mailpieces point at the address row
written on the envelope instead of a version snapshot. Mailings become
editions.

Both directions read every row into memory first, swap the schema, then load
the mapped rows back.

Revision ID: 9157d1772fb8
Revises: 5ea8adf60cba
Create Date: 2026-07-26 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "9157d1772fb8"
down_revision = "5ea8adf60cba"
branch_labels = None
depends_on = None


def _create_new_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "active", "unsubscribed", name="userstatus"),
            nullable=False,
        ),
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
        "editions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
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


def _create_old_tables() -> None:
    op.create_table(
        "mailings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "recipients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address_line1", sa.String(length=200), nullable=False),
        sa.Column("address_line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("recipients", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_recipients_email"), ["email"], unique=True
        )

    op.create_table(
        "recipient_sessions",
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column("recipient_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"]),
        sa.PrimaryKeyConstraint("token"),
    )

    op.create_table(
        "recipient_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recipient_id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address_line1", sa.String(length=200), nullable=False),
        sa.Column("address_line2", sa.String(length=200), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=False),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("recipient_versions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_recipient_versions_recipient_id"),
            ["recipient_id"],
            unique=False,
        )

    op.create_table(
        "mailpieces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mailing_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_version_id", sa.String(length=36), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["mailing_id"], ["mailings.id"]),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"]),
        sa.ForeignKeyConstraint(["recipient_version_id"], ["recipient_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mailing_id", "recipient_id"),
    )
    with op.batch_alter_table("mailpieces", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_mailpieces_mailing_id"), ["mailing_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mailpieces_recipient_id"), ["recipient_id"], unique=False
        )


def _read_old_rows(bind) -> dict[str, list[dict]]:
    """The old schema's rows, mapped onto the new schema's shape."""
    users = [
        {
            "id": r.id,
            "email": r.email,
            "status": (
                "pending"
                if r.verified_at is None
                else "unsubscribed"
                if r.unsubscribed_at is not None
                else "active"
            ),
            "verified_at": r.verified_at,
            "unsubscribed_at": r.unsubscribed_at,
            "created_at": r.created_at,
        }
        for r in bind.execute(
            sa.text(
                "SELECT id, email, verified_at, unsubscribed_at, created_at"
                " FROM recipients"
            )
        )
    ]
    addresses: list[dict] = []
    version_to_address: dict[str, str] = {}
    latest_of_user: dict[str, tuple] = {}
    versions = bind.execute(
        sa.text(
            "SELECT id, recipient_id, name, address_line1, address_line2, city,"
            " region, postal_code, country, valid_from"
            " FROM recipient_versions ORDER BY recipient_id, valid_from, id"
        )
    )
    for row in versions:
        fields = tuple(row[2:9])
        current = latest_of_user.get(row.recipient_id)
        if current is None or current[0] != fields:
            current = (fields, row.id)
            latest_of_user[row.recipient_id] = current
            addresses.append(
                {
                    "id": current[1],
                    "user_id": row.recipient_id,
                    "addressee": fields[0],
                    "address_line1": fields[1],
                    "address_line2": fields[2],
                    "city": fields[3],
                    "region": fields[4],
                    "postal_code": fields[5],
                    "country": fields[6],
                    "derived_from_id": None,
                    "created_at": row.valid_from,
                }
            )
        version_to_address[row.id] = current[1]
    editions = [
        {"id": m.id, "title": m.title, "created_at": m.created_at}
        for m in bind.execute(sa.text("SELECT id, title, created_at FROM mailings"))
    ]
    user_sessions = [
        {"token": s.token, "user_id": s.recipient_id, "created_at": s.created_at}
        for s in bind.execute(
            sa.text("SELECT token, recipient_id, created_at FROM recipient_sessions")
        )
    ]
    mailpieces = [
        {
            "id": m.id,
            "edition_id": m.mailing_id,
            "user_id": m.recipient_id,
            "address_id": version_to_address[m.recipient_version_id],
            "sent_at": m.sent_at,
        }
        for m in bind.execute(
            sa.text(
                "SELECT id, mailing_id, recipient_id, recipient_version_id, sent_at"
                " FROM mailpieces"
            )
        )
    ]
    return {
        "users": users,
        "addresses": addresses,
        "editions": editions,
        "user_sessions": user_sessions,
        "mailpieces": mailpieces,
    }


def _read_new_rows(bind) -> dict[str, list[dict]]:
    """The new schema's rows, mapped onto the old schema's shape."""
    versions: list[dict] = []
    address_rows = bind.execute(
        sa.text(
            "SELECT a.id, a.user_id, u.email, a.addressee, a.address_line1,"
            " a.address_line2, a.city, a.region, a.postal_code, a.country,"
            " a.created_at"
            " FROM addresses a JOIN users u ON u.id = a.user_id"
            " ORDER BY a.user_id, a.created_at, a.id"
        )
    )
    for a in address_rows:
        versions.append(
            {
                "id": a.id,
                "recipient_id": a.user_id,
                "email": a.email,
                "name": a.addressee,
                "address_line1": a.address_line1,
                "address_line2": a.address_line2,
                "city": a.city,
                "region": a.region,
                "postal_code": a.postal_code,
                "country": a.country,
                "valid_from": a.created_at,
            }
        )
    latest_version_of: dict[str, dict] = {}
    for version in versions:
        latest_version_of[version["recipient_id"]] = version
    recipients = []
    for u in bind.execute(
        sa.text("SELECT id, email, created_at, verified_at, unsubscribed_at FROM users")
    ):
        latest = latest_version_of.get(u.id)
        if latest is None:
            continue
        recipients.append(
            {
                "id": u.id,
                "email": u.email,
                "name": latest["name"],
                "address_line1": latest["address_line1"],
                "address_line2": latest["address_line2"],
                "city": latest["city"],
                "region": latest["region"],
                "postal_code": latest["postal_code"],
                "country": latest["country"],
                "created_at": u.created_at,
                "verified_at": u.verified_at,
                "unsubscribed_at": u.unsubscribed_at,
            }
        )
    mailings = [
        {"id": m.id, "title": m.title, "created_at": m.created_at}
        for m in bind.execute(sa.text("SELECT id, title, created_at FROM editions"))
    ]
    recipient_sessions = [
        {"token": s.token, "recipient_id": s.user_id, "created_at": s.created_at}
        for s in bind.execute(
            sa.text("SELECT token, user_id, created_at FROM user_sessions")
        )
    ]
    mailpieces = [
        {
            "id": m.id,
            "mailing_id": m.edition_id,
            "recipient_id": m.user_id,
            "recipient_version_id": m.address_id,
            "sent_at": m.sent_at,
        }
        for m in bind.execute(
            sa.text("SELECT id, edition_id, user_id, address_id, sent_at FROM mailpieces")
        )
    ]
    return {
        "recipients": recipients,
        "recipient_versions": versions,
        "mailings": mailings,
        "recipient_sessions": recipient_sessions,
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
    for table in (
        "mailpieces",
        "recipient_versions",
        "recipient_sessions",
        "recipients",
        "mailings",
    ):
        op.drop_table(table)
    _create_new_tables()
    for table in ("users", "addresses", "editions", "user_sessions", "mailpieces"):
        _insert_rows(bind, table, snapshot[table])


def downgrade() -> None:
    bind = op.get_bind()
    snapshot = _read_new_rows(bind)
    for table in ("mailpieces", "user_sessions", "addresses", "editions", "users"):
        op.drop_table(table)
    _create_old_tables()
    for table in (
        "recipients",
        "recipient_versions",
        "mailings",
        "recipient_sessions",
        "mailpieces",
    ):
        _insert_rows(bind, table, snapshot[table])
