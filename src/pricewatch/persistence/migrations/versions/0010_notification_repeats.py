"""Счётчик повторов уведомления (notifications.repeat_count): одинаковый инцидент склеивается по dedup_key.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('notifications', sa.Column('repeat_count', sa.Integer(), server_default='1', nullable=False))
    op.create_check_constraint(op.f('ck_notifications_repeat_count'), 'notifications', 'repeat_count >= 1')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_notifications_repeat_count'), 'notifications', type_='check')
    op.drop_column('notifications', 'repeat_count')
