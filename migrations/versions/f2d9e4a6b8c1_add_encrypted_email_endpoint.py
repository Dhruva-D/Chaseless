"""Add encrypted customer email endpoint.

Revision ID: f2d9e4a6b8c1
Revises: c9f1b3e4a7d2
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "f2d9e4a6b8c1"
down_revision = "c9f1b3e4a7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("email_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "email_encrypted")
