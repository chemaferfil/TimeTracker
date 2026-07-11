"""Add overtime_alert table

Revision ID: 20260712_01
Revises: 20240902_01
Create Date: 2026-07-12 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260712_01"
down_revision = "20240902_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "overtime_alert",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("worked_seconds", sa.Integer(), nullable=False),
        sa.Column("expected_seconds", sa.Integer(), nullable=False),
        sa.Column("excess_seconds", sa.Integer(), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "date", name="uix_overtime_user_date"),
    )


def downgrade():
    op.drop_table("overtime_alert")
