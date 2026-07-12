"""challenges table

Revision ID: 4f8e21c7ab30
Revises: db223944cf69
Create Date: 2026-07-12 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "4f8e21c7ab30"
down_revision = "db223944cf69"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("challenger_id", sa.BigInteger(), nullable=False),
        sa.Column("challenger_username", sa.String(length=64), nullable=False),
        sa.Column("opponent_username", sa.String(length=64), nullable=False),
        sa.Column("opponent_id", sa.BigInteger(), nullable=True),
        sa.Column("market_title", sa.String(length=200), nullable=False),
        sa.Column("yes_token", sa.String(length=80), nullable=False),
        sa.Column("no_token", sa.String(length=80), nullable=False),
        sa.Column("challenger_side", sa.String(length=4), nullable=False),
        sa.Column("size_usdc", sa.Numeric(precision=20, scale=6), nullable=False),
        sa.Column("challenger_update_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("winner_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["challenger_id"],
            ["users.telegram_user_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_challenges_chat_id"), "challenges", ["chat_id"], unique=False)
    op.create_index(op.f("ix_challenges_status"), "challenges", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_challenges_status"), table_name="challenges")
    op.drop_index(op.f("ix_challenges_chat_id"), table_name="challenges")
    op.drop_table("challenges")
