"""Add weekly hours history periods

Revision ID: 20260615_01
Revises: 20240902_01
Create Date: 2026-06-15 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260615_01"
down_revision = "20240902_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_weekly_hours_period",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("weekly_hours", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_user_weekly_hours_dates",
        ),
        sa.CheckConstraint(
            "weekly_hours >= 0",
            name="ck_user_weekly_hours_non_negative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "start_date",
            name="uix_user_weekly_hours_start",
        ),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            INSERT INTO user_weekly_hours_period (
                user_id,
                weekly_hours,
                start_date,
                end_date,
                created_at,
                updated_at
            )
            SELECT
                u.id,
                COALESCE(u.weekly_hours, 0),
                COALESCE(
                    u.hire_date,
                    first_records.first_date,
                    u.created_at::date,
                    CURRENT_DATE
                ),
                NULL,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM "user" u
            LEFT JOIN (
                SELECT user_id, MIN(date) AS first_date
                FROM time_record
                GROUP BY user_id
            ) first_records ON first_records.user_id = u.id
            ON CONFLICT DO NOTHING
        """)
    else:
        op.execute("""
            INSERT INTO user_weekly_hours_period (
                user_id,
                weekly_hours,
                start_date,
                end_date,
                created_at,
                updated_at
            )
            SELECT
                u.id,
                COALESCE(u.weekly_hours, 0),
                COALESCE(
                    u.hire_date,
                    first_records.first_date,
                    DATE(u.created_at),
                    DATE('now')
                ),
                NULL,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM "user" u
            LEFT JOIN (
                SELECT user_id, MIN(date) AS first_date
                FROM time_record
                GROUP BY user_id
            ) first_records ON first_records.user_id = u.id
        """)


def downgrade():
    op.drop_table("user_weekly_hours_period")
