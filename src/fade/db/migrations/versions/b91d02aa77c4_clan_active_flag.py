"""clan active flag

Revision ID: b91d02aa77c4
Revises: 4f8e21c7ab30
Create Date: 2026-07-12 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b91d02aa77c4"
down_revision = "4f8e21c7ab30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clans",
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("clans", "active")
