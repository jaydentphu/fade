"""user referred_by

Revision ID: 0a1b2c3d4e5f
Revises: f1e2d3c4b5a6
Create Date: 2026-07-14 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "0a1b2c3d4e5f"
down_revision = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referred_by", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "referred_by")
