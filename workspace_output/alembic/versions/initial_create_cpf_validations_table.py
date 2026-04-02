"""
create cpf_validations table

Revision ID: initial
Revises: 
Create Date: 2024-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'initial'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'cpf_validations',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('cpf', sa.String(length=11), nullable=False),
        sa.Column('is_valid', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )

def downgrade():
    op.drop_table('cpf_validations')
