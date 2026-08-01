"""soft-deleted editions

Edition rows gain an archive timestamp.

Revision ID: 4fd713a86b9c
Revises: c8f3a1d47b2e
Create Date: 2026-08-01 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


revision = "4fd713a86b9c"
down_revision = "c8f3a1d47b2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("editions", sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("editions", schema=None) as batch_op:
        batch_op.drop_column("deleted_at")
