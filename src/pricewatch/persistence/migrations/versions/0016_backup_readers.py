"""Домашние приёмники копий (backup_readers) и неудачные попытки их токена (auth_failures.kind = 'backup').

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0016'
down_revision: str | None = '0015'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HASH = "'^[0-9a-f]{64}$'"


def upgrade() -> None:
    op.create_table('backup_readers',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('next_token_hash', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_pulled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(f'token_hash ~ {HASH}', name=op.f('ck_backup_readers_token_hash_format')),
    sa.CheckConstraint(f'next_token_hash IS NULL OR next_token_hash ~ {HASH}', name=op.f('ck_backup_readers_next_token_hash_format')),
    sa.CheckConstraint('next_token_hash IS NULL OR next_token_hash <> token_hash', name=op.f('ck_backup_readers_tokens_differ')),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 80', name=op.f('ck_backup_readers_name_length')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backup_readers')),
    sa.UniqueConstraint('name', name=op.f('uq_backup_readers_name')),
    sa.UniqueConstraint('next_token_hash', name=op.f('uq_backup_readers_next_token_hash')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_backup_readers_token_hash'))
    )
    op.drop_constraint(op.f('ck_auth_failures_kind'), 'auth_failures', type_='check')
    op.create_check_constraint(op.f('ck_auth_failures_kind'), 'auth_failures', "kind IN ('login', 'pairing', 'backup')")


def downgrade() -> None:
    op.execute("DELETE FROM auth_failures WHERE kind = 'backup'")
    op.drop_constraint(op.f('ck_auth_failures_kind'), 'auth_failures', type_='check')
    op.create_check_constraint(op.f('ck_auth_failures_kind'), 'auth_failures', "kind IN ('login', 'pairing')")
    op.drop_table('backup_readers')
