"""mail undelivered workflow

Revision ID: 7da58291ef01
Revises: 2b7f4c8d1e93
Create Date: 2026-08-27 00:00:00.000000

"""

from uuid6 import uuid7

import sqlalchemy as sa
from alembic import op


revision = "7da58291ef01"
down_revision = "2b7f4c8d1e93"
branch_labels = None
depends_on = None

NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s"
}


def upgrade() -> None:
    with op.batch_alter_table(
        "mailpieces", naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(
            "uq_mailpieces_edition_id_user_id", type_="unique"
        )

    op.create_table(
        "returns",
        sa.Column("mailpiece_id", sa.String(length=36), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mailpiece_id"], ["mailpieces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("mailpiece_id"),
    )
    op.create_table(
        "drafts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("edition_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["edition_id"], ["editions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_drafts_edition_id"),
        "drafts",
        ["edition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_drafts_user_id"),
        "drafts",
        ["user_id"],
        unique=False,
    )
    drafts = sa.table(
        "drafts",
        sa.column("id", sa.String(length=36)),
        sa.column("edition_id", sa.String(length=36)),
        sa.column("user_id", sa.String(length=36)),
    )
    candidates = op.get_bind().execute(
        sa.text(
            """
        SELECT editions.id, users.id
        FROM editions
        JOIN users
          ON users.created_at <= editions.created_at
         AND (
              users.unsubscribed_at IS NULL
              OR users.unsubscribed_at > editions.created_at
         )
        WHERE NOT EXISTS (
            SELECT 1
            FROM mailpieces
            WHERE mailpieces.edition_id = editions.id
              AND mailpieces.user_id = users.id
        )
        """
        )
    )
    op.bulk_insert(
        drafts,
        [
            {"id": str(uuid7()), "edition_id": edition_id, "user_id": user_id}
            for edition_id, user_id in candidates
        ],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_drafts_user_id"),
        table_name="drafts",
    )
    op.drop_index(
        op.f("ix_drafts_edition_id"),
        table_name="drafts",
    )
    op.drop_table("drafts")
    op.drop_table("returns")

    with op.batch_alter_table("mailpieces") as batch_op:
        batch_op.create_unique_constraint(
            "uq_mailpieces_edition_id_user_id", ["edition_id", "user_id"]
        )
