"""initial schema

Users, their append-only addresses, editions, and the mailpieces joining them,
alongside the login codes and admin sessions the sign-in flows keep.

Revision ID: 9157d1772fb8
Revises:
Create Date: 2026-07-26 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "9157d1772fb8"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("login_codes", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_login_codes_email"), ["email"], unique=False
        )

    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

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


def downgrade() -> None:
    for table in (
        "mailpieces",
        "user_sessions",
        "editions",
        "addresses",
        "users",
        "admin_sessions",
        "login_codes",
    ):
        op.drop_table(table)
