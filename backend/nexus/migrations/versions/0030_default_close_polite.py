"""default_close_polite

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-07

Flips the ``tenant_settings.engine_knockout_policy`` column server_default
from ``'record_only'`` to ``'close_polite'``.

Rationale: enterprise screening expects "candidate fails a hard requirement
→ interview ends politely". The original migration 0027 default was
``'record_only'`` for safety during initial rollout; that bias was wrong
and led to a real incident where two knockout signals were disclosed and
the agent kept going. ``'close_polite'`` is the correct floor; tenants
that want to keep gathering data flip the column explicitly.

Existing rows are NOT auto-migrated. We have no way to distinguish a row
that was explicitly set to ``'record_only'`` by an operator from a row
that inherited the default — silently overwriting the former would be a
trust violation. New rows inserted after this migration pick up
``'close_polite'`` via the new server_default.
"""

from alembic import op
import sqlalchemy as sa


revision: str = "0030"
down_revision: str | None = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tenant_settings",
        "engine_knockout_policy",
        server_default=sa.text("'close_polite'"),
        existing_type=sa.String(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "tenant_settings",
        "engine_knockout_policy",
        server_default=sa.text("'record_only'"),
        existing_type=sa.String(),
        existing_nullable=False,
    )
