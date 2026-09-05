"""add recovery execution idempotency

Revision ID: 8a2c7a6f2a01
Revises: 37b96b5b6c8b
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8a2c7a6f2a01"
down_revision: str | None = "37b96b5b6c8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("recovery_runs")}
    if "execute_idempotency_key" not in columns:
        op.add_column(
            "recovery_runs",
            sa.Column("execute_idempotency_key", sa.String(length=200), nullable=True),
        )
    indexes = {index["name"] for index in inspector.get_indexes("recovery_runs")}
    if "uq_recovery_runs_execute_idempotency_key" not in indexes:
        op.create_index(
            "uq_recovery_runs_execute_idempotency_key",
            "recovery_runs",
            ["execute_idempotency_key"],
            unique=True,
        )


def downgrade() -> None:
    op.drop_index("uq_recovery_runs_execute_idempotency_key", table_name="recovery_runs")
    op.drop_column("recovery_runs", "execute_idempotency_key")
