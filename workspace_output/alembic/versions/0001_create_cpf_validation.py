"""create cpf_validation table

Revision ID: 0001
Revises: 
Create Date: 2024-01-01 00:00:00.000000
"""

# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        'cpf_validation',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('cpf', sa.String(length=11), nullable=False),
        sa.Column('valid', sa.Boolean(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table('cpf_validation')
