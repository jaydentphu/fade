"""withdrawals table

Revision ID: c7d3e91f0a24
Revises: b91d02aa77c4
Create Date: 2026-07-12 16:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "c7d3e91f0a24"
down_revision = "b91d02aa77c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("address", sa.String(length=42), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("tx_hash", sa.String(length=66), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["telegram_user_id"],
            ["users.telegram_user_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_withdrawals_telegram_user_id"), "withdrawals", ["telegram_user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_withdrawals_telegram_user_id"), table_name="withdrawals")
    op.drop_table("withdrawals")
