"""Ключи повтора изменяющих запросов приложения (idempotency_keys).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('idempotency_keys',
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('request_hash', sa.Text(), nullable=False),
    sa.Column('response_status', sa.Integer(), nullable=True),
    sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('char_length(key) BETWEEN 8 AND 64', name=op.f('ck_idempotency_keys_key_length')),
    sa.CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_idempotency_keys_request_hash_format')),
    sa.CheckConstraint('response_status IS NULL OR response_status BETWEEN 200 AND 299', name=op.f('ck_idempotency_keys_response_status')),
    sa.CheckConstraint('(response_status IS NULL) = (response_body IS NULL)', name=op.f('ck_idempotency_keys_response_together')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_idempotency_keys_user_id')),
    sa.PrimaryKeyConstraint('user_id', 'key', name=op.f('pk_idempotency_keys'))
    )
    op.create_index('ix_idempotency_keys_created_at', 'idempotency_keys', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_idempotency_keys_created_at', table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
