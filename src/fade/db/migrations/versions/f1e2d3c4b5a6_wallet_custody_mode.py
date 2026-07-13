"""wallet custody mode

Revision ID: f1e2d3c4b5a6
Revises: c7d3e91f0a24
Create Date: 2026-07-13 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f1e2d3c4b5a6"
down_revision = "c7d3e91f0a24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wallets",
        sa.Column("custody", sa.String(length=16), nullable=False, server_default="server"),
    )


def downgrade() -> None:
    op.drop_column("wallets", "custody")
