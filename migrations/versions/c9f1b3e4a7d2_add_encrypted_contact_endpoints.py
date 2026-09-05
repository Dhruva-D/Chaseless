"""Add encrypted customer contact endpoints.

Revision ID: c9f1b3e4a7d2
Revises: 8a2c7a6f2a01
Create Date: 2026-09-01
"""

from alembic import op
import sqlalchemy as sa

revision = "c9f1b3e4a7d2"
down_revision = "8a2c7a6f2a01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customers", sa.Column("phone_e164_encrypted", sa.Text(), nullable=True))
    op.add_column("customers", sa.Column("whatsapp_e164_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("customers", "whatsapp_e164_encrypted")
    op.drop_column("customers", "phone_e164_encrypted")
