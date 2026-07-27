"""users, addresses, editions; mailpieces repointed to addresses

Recipients become users with an explicit subscription status. Recipient
versions become append-only addresses; mailpieces point at the address row
written on the envelope instead of a version snapshot. Mailings become
editions.

Revision ID: 9157d1772fb8
Revises: 5ea8adf60cba
Create Date: 2026-07-26 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from uuid6 import uuid7

revision = "9157d1772fb8"
down_revision = "5ea8adf60cba"
branch_labels = None
depends_on = None

_LABEL_FIELDS = (
    "name",
    "address_line1",
    "address_line2",
    "city",
    "region",
    "postal_code",
    "country",
)


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


def _create_mailpieces_table(name: str) -> None:
    op.create_table(
        name,
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
    with op.batch_alter_table(name, schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_mailpieces_edition_id"), ["edition_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_mailpieces_user_id"), ["user_id"], unique=False
        )


def _backfill() -> list[dict]:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            INSERT INTO users (id, email, status, verified_at, unsubscribed_at, created_at)
            SELECT id, email,
                   CASE
                       WHEN verified_at IS NULL THEN 'pending'
                       WHEN unsubscribed_at IS NOT NULL THEN 'unsubscribed'
                       ELSE 'active'
                   END,
                   verified_at, unsubscribed_at, created_at
            FROM recipients
            """
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO editions (id, title, created_at)"
            " SELECT id, title, created_at FROM mailings"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO user_sessions (token, user_id, created_at)"
            " SELECT token, recipient_id, created_at FROM recipient_sessions"
        )
    )

    versions = bind.execute(
        sa.text(
            "SELECT id, recipient_id, name, address_line1, address_line2, city,"
            " region, postal_code, country, valid_from"
            " FROM recipient_versions ORDER BY recipient_id, valid_from, id"
        )
    ).all()

    version_to_address: dict[str, str] = {}
    latest_of_user: dict[str, tuple] = {}
    for row in versions:
        fields = tuple(row[2:9])
        current = latest_of_user.get(row.recipient_id)
        if current is None or current[0] != fields:
            address_id = str(uuid7())
            latest_of_user[row.recipient_id] = (fields, address_id)
            bind.execute(
                sa.text(
                    "INSERT INTO addresses (id, user_id, addressee, address_line1,"
                    " address_line2, city, region, postal_code, country,"
                    " derived_from_id, created_at)"
                    " VALUES (:id, :user_id, :addressee, :line1, :line2, :city,"
                    " :region, :postal_code, :country, NULL, :created_at)"
                ),
                {
                    "id": address_id,
                    "user_id": row.recipient_id,
                    "addressee": fields[0],
                    "line1": fields[1],
                    "line2": fields[2],
                    "city": fields[3],
                    "region": fields[4],
                    "postal_code": fields[5],
                    "country": fields[6],
                    "created_at": row.valid_from,
                },
            )
        version_to_address[row.id] = latest_of_user[row.recipient_id][1]

    mailpieces = []
    for mailpiece in bind.execute(
        sa.text(
            "SELECT id, mailing_id, recipient_id, recipient_version_id, sent_at"
            " FROM mailpieces"
        )
    ).all():
        mailpieces.append(
            {
                "id": mailpiece.id,
                "edition_id": mailpiece.mailing_id,
                "user_id": mailpiece.recipient_id,
                "address_id": version_to_address[mailpiece.recipient_version_id],
                "sent_at": mailpiece.sent_at,
            }
        )
    return mailpieces


def upgrade() -> None:
    _create_new_tables()
    mailpieces = _backfill()
    op.drop_table("mailpieces")
    _create_mailpieces_table("mailpieces")
    bind = op.get_bind()
    for mailpiece in mailpieces:
        bind.execute(
            sa.text(
                "INSERT INTO mailpieces (id, edition_id, user_id, address_id, sent_at)"
                " VALUES (:id, :edition_id, :user_id, :address_id, :sent_at)"
            ),
            mailpiece,
        )
    op.drop_table("recipient_versions")
    op.drop_table("recipient_sessions")
    op.drop_table("recipients")
    op.drop_table("mailings")


def downgrade() -> None:
    bind = op.get_bind()

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
        "mailings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "recipient_sessions",
        sa.Column("token", sa.String(length=43), nullable=False),
        sa.Column("recipient_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"]),
        sa.PrimaryKeyConstraint("token"),
    )

    address_to_version: dict[str, str] = {}
    for address in bind.execute(
        sa.text(
            "SELECT a.id, a.user_id, u.email, a.addressee, a.address_line1,"
            " a.address_line2, a.city, a.region, a.postal_code, a.country,"
            " a.created_at"
            " FROM addresses a JOIN users u ON u.id = a.user_id"
            " ORDER BY a.user_id, a.created_at, a.id"
        )
    ).all():
        version_id = str(uuid7())
        address_to_version[address.id] = version_id
        bind.execute(
            sa.text(
                "INSERT INTO recipient_versions (id, recipient_id, email, name,"
                " address_line1, address_line2, city, region, postal_code,"
                " country, valid_from)"
                " VALUES (:id, :user_id, :email, :name, :line1, :line2, :city,"
                " :region, :postal_code, :country, :valid_from)"
            ),
            {
                "id": version_id,
                "user_id": address.user_id,
                "email": address.email,
                "name": address.addressee,
                "line1": address.address_line1,
                "line2": address.address_line2,
                "city": address.city,
                "region": address.region,
                "postal_code": address.postal_code,
                "country": address.country,
                "valid_from": address.created_at,
            },
        )

    bind.execute(
        sa.text(
            """
            INSERT INTO recipients (id, email, name, address_line1, address_line2,
                                    city, region, postal_code, country,
                                    created_at, verified_at, unsubscribed_at)
            SELECT u.id, u.email, v.name, v.address_line1, v.address_line2,
                   v.city, v.region, v.postal_code, v.country,
                   u.created_at, u.verified_at, u.unsubscribed_at
            FROM users u
            JOIN recipient_versions v ON v.id = (
                SELECT id FROM recipient_versions
                WHERE recipient_id = u.id
                ORDER BY valid_from DESC, id DESC LIMIT 1
            )
            """
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO mailings (id, title, created_at)"
            " SELECT id, title, created_at FROM editions"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO recipient_sessions (token, recipient_id, created_at)"
            " SELECT token, user_id, created_at FROM user_sessions"
        )
    )
    mailpieces = []
    for mailpiece in bind.execute(
        sa.text(
            "SELECT id, edition_id, user_id, address_id, sent_at FROM mailpieces"
        )
    ).all():
        mailpieces.append(
            {
                "id": mailpiece.id,
                "mailing_id": mailpiece.edition_id,
                "recipient_id": mailpiece.user_id,
                "version_id": address_to_version[mailpiece.address_id],
                "sent_at": mailpiece.sent_at,
            }
        )

    op.drop_table("mailpieces")
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
    for mailpiece in mailpieces:
        bind.execute(
            sa.text(
                "INSERT INTO mailpieces (id, mailing_id, recipient_id,"
                " recipient_version_id, sent_at)"
                " VALUES (:id, :mailing_id, :recipient_id, :version_id, :sent_at)"
            ),
            mailpiece,
        )
    op.drop_table("user_sessions")
    op.drop_table("editions")
    op.drop_table("addresses")
    op.drop_table("users")
