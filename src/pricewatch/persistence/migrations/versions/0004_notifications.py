"""Уведомления: Telegram и колокольчик администраторов, склейка одинаковых по dedup_key.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('notifications',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('dedup_key', sa.Text(), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("channel IN ('telegram', 'in_app')", name=op.f('ck_notifications_channel')),
    sa.CheckConstraint("kind ~ '^[a-z_]{1,60}$'", name=op.f('ck_notifications_kind_format')),
    sa.CheckConstraint('char_length(dedup_key) BETWEEN 1 AND 200', name=op.f('ck_notifications_dedup_key_length')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_notifications_attempts')),
    sa.CheckConstraint("sent_at IS NULL OR channel = 'telegram'", name=op.f('ck_notifications_sent_only_telegram')),
    sa.CheckConstraint("read_at IS NULL OR channel = 'in_app'", name=op.f('ck_notifications_read_only_in_app')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('user_id', 'channel', 'dedup_key', name=op.f('uq_notifications_user_id_channel_dedup_key'))
    )


def downgrade() -> None:
    op.drop_table('notifications')
