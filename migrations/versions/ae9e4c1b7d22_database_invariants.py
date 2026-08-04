"""database invariants

Ensure every mailpiece's normalized address belongs to its user and remove
unused login attempt counters.

Revision ID: ae9e4c1b7d22
Revises: 4fd713a86b9c
Create Date: 2026-08-03 22:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


revision = "ae9e4c1b7d22"
down_revision = "4fd713a86b9c"
branch_labels = None
depends_on = None


NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
}


def upgrade() -> None:
    bind = op.get_bind()
    mismatched_mailpieces = [
        row.id
        for row in bind.execute(
            sa.text(
                "SELECT mailpieces.id FROM mailpieces"
                " JOIN normalized_addresses"
                " ON normalized_addresses.id = mailpieces.normalized_address_id"
                " JOIN addresses"
                " ON addresses.id = normalized_addresses.address_id"
                " WHERE mailpieces.user_id != addresses.user_id"
            )
        )
    ]
    if mismatched_mailpieces:
        raise RuntimeError(
            "Mailpieces carrying another user's address: "
            + ", ".join(sorted(mismatched_mailpieces))
        )

    with op.batch_alter_table("addresses", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_addresses_id_user_id", ["id", "user_id"]
        )

    op.add_column(
        "normalized_addresses",
        sa.Column("user_id", sa.String(length=36), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE normalized_addresses"
            " SET user_id = ("
            " SELECT addresses.user_id FROM addresses"
            " WHERE addresses.id = normalized_addresses.address_id"
            " )"
        )
    )
    with op.batch_alter_table(
        "normalized_addresses",
        schema=None,
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_normalized_addresses_address_id_addresses", type_="foreignkey"
        )
        batch_op.alter_column(
            "user_id", existing_type=sa.String(length=36), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_normalized_addresses_id_user_id", ["id", "user_id"]
        )
        batch_op.create_foreign_key(
            "fk_normalized_addresses_address_user",
            "addresses",
            ["address_id", "user_id"],
            ["id", "user_id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table(
        "mailpieces", schema=None, naming_convention=NAMING_CONVENTION
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_mailpieces_normalized_address_id_normalized_addresses",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_mailpieces_normalized_address_user",
            "normalized_addresses",
            ["normalized_address_id", "user_id"],
            ["id", "user_id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("login_codes", schema=None) as batch_op:
        batch_op.drop_column("attempts")


def downgrade() -> None:
    op.add_column(
        "login_codes", sa.Column("attempts", sa.Integer(), nullable=True)
    )
    op.execute(sa.text("UPDATE login_codes SET attempts = 0"))
    with op.batch_alter_table("login_codes", schema=None) as batch_op:
        batch_op.alter_column(
            "attempts", existing_type=sa.Integer(), nullable=False
        )

    with op.batch_alter_table("mailpieces", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_mailpieces_normalized_address_user", type_="foreignkey"
        )
        batch_op.create_foreign_key(
            "fk_mailpieces_normalized_address_id_normalized_addresses",
            "normalized_addresses",
            ["normalized_address_id"],
            ["id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("normalized_addresses", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_normalized_addresses_address_user", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "uq_normalized_addresses_id_user_id", type_="unique"
        )
        batch_op.create_foreign_key(
            "fk_normalized_addresses_address_id_addresses",
            "addresses",
            ["address_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("user_id")

    with op.batch_alter_table("addresses", schema=None) as batch_op:
        batch_op.drop_constraint("uq_addresses_id_user_id", type_="unique")
