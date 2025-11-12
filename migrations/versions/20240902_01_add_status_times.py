"""Add entry and exit times to employee status

Revision ID: 20240902_01
Revises: 
Create Date: 2024-09-02 00:00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20240902_01'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('employee_status', sa.Column('entry_time', sa.Time(), nullable=True))
    op.add_column('employee_status', sa.Column('exit_time', sa.Time(), nullable=True))

def downgrade():
    op.drop_column('employee_status', 'exit_time')
    op.drop_column('employee_status', 'entry_time')
