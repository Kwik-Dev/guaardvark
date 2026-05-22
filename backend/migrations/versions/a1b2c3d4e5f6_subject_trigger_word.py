"""add trigger_word to subjects

The rare token a character LoRA is trained on and must be prompted with at
inference (e.g. "sage_harlow"). Locks identity across generations.
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '02d047e9bd68'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('subjects', sa.Column('trigger_word', sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column('subjects', 'trigger_word')
