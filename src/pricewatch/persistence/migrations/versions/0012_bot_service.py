"""Telegram-бот: служебный токен (services), коды привязки Telegram, захват сообщений ботом (notifications.claimed_until).

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HASH = "'^[0-9a-f]{64}$'"


def upgrade() -> None:
    op.create_table('services',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('next_token_hash', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(f'token_hash ~ {HASH}', name=op.f('ck_services_token_hash_format')),
    sa.CheckConstraint(f'next_token_hash IS NULL OR next_token_hash ~ {HASH}', name=op.f('ck_services_next_token_hash_format')),
    sa.CheckConstraint('next_token_hash IS NULL OR next_token_hash <> token_hash', name=op.f('ck_services_tokens_differ')),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 80', name=op.f('ck_services_name_length')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_services')),
    sa.UniqueConstraint('name', name=op.f('uq_services_name')),
    sa.UniqueConstraint('next_token_hash', name=op.f('uq_services_next_token_hash')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_services_token_hash'))
    )
    op.create_table('telegram_link_codes',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('code_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(f'code_hash ~ {HASH}', name=op.f('ck_telegram_link_codes_code_hash_format')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_telegram_link_codes_expires_after_created')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_telegram_link_codes_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_telegram_link_codes')),
    sa.UniqueConstraint('code_hash', name=op.f('uq_telegram_link_codes_code_hash'))
    )
    op.create_index('ix_telegram_link_codes_user_id', 'telegram_link_codes', ['user_id'], unique=False)
    op.add_column('notifications', sa.Column('claimed_until', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(op.f('ck_notifications_claimed_only_telegram'), 'notifications', "claimed_until IS NULL OR channel = 'telegram'")


def downgrade() -> None:
    op.drop_constraint(op.f('ck_notifications_claimed_only_telegram'), 'notifications', type_='check')
    op.drop_column('notifications', 'claimed_until')
    op.drop_index('ix_telegram_link_codes_user_id', table_name='telegram_link_codes')
    op.drop_table('telegram_link_codes')
    op.drop_table('services')
