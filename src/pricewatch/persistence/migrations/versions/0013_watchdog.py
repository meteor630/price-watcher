"""Сторож: начало молчания worker и сервиса, о котором администраторам уже сообщено (workers.silent_since, services.silent_since).

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('workers', sa.Column('silent_since', sa.DateTime(timezone=True), nullable=True))
    op.add_column('services', sa.Column('silent_since', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('services', 'silent_since')
    op.drop_column('workers', 'silent_since')
