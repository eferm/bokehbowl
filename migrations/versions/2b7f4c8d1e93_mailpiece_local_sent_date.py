"""mailpiece local sent date

Keep the operator-local mailing date alongside the UTC audit timestamp.
Existing dates are derived once using OPERATOR_TZ so they do not change
if the instance timezone changes later.

Revision ID: 2b7f4c8d1e93
Revises: ae9e4c1b7d22
Create Date: 2026-08-03 23:00:00.000000

"""

import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op


revision = "2b7f4c8d1e93"
down_revision = "ae9e4c1b7d22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    timezone = ZoneInfo(os.environ["OPERATOR_TZ"])
    # Existing rows need values before SQLite can enforce NOT NULL.
    op.add_column("mailpieces", sa.Column("sent_on", sa.Date(), nullable=True))

    mailpieces = sa.table(
        "mailpieces",
        sa.column("id", sa.String(length=36)),
        sa.column("sent_at", sa.DateTime()),
        sa.column("sent_on", sa.Date()),
    )
    bind = op.get_bind()
    for mailpiece_id, sent_at in bind.execute(
        sa.select(mailpieces.c.id, mailpieces.c.sent_at)
    ):
        if isinstance(sent_at, str):
            sent_at = datetime.fromisoformat(sent_at)
        sent_on = sent_at.replace(tzinfo=UTC).astimezone(timezone).date()
        bind.execute(
            mailpieces.update()
            .where(mailpieces.c.id == mailpiece_id)
            .values(sent_on=sent_on)
        )

    with op.batch_alter_table("mailpieces", schema=None) as batch_op:
        batch_op.alter_column(
            "sent_on", existing_type=sa.Date(), nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("mailpieces", schema=None) as batch_op:
        batch_op.drop_column("sent_on")
