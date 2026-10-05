"""Вход: приглашения, сессии сайта, устройства, токены, коды связки, неудачные попытки.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('auth_failures',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('subject', sa.Text(), nullable=True),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('login', 'pairing')", name=op.f('ck_auth_failures_kind')),
    sa.CheckConstraint('subject IS NULL OR char_length(subject) BETWEEN 1 AND 64', name=op.f('ck_auth_failures_subject_length')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_failures'))
    )
    op.create_index('ix_auth_failures_kind_ip_created_at', 'auth_failures', ['kind', 'ip', 'created_at'], unique=False)
    op.create_index('ix_auth_failures_kind_subject_created_at', 'auth_failures', ['kind', 'subject', 'created_at'], unique=False)
    op.create_table('devices',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 80', name=op.f('ck_devices_name_length')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_devices_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_devices'))
    )
    op.create_index('ix_devices_user_id', 'devices', ['user_id'], unique=False)
    op.create_table('invites',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('code_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('created_by', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_by', sa.BigInteger(), nullable=True),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("code_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_invites_code_hash_format')),
    sa.CheckConstraint("role IN ('user', 'admin')", name=op.f('ck_invites_role')),
    sa.CheckConstraint('(used_by IS NULL) = (used_at IS NULL)', name=op.f('ck_invites_used_together')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_invites_expires_after_created')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_invites_created_by')),
    sa.ForeignKeyConstraint(['used_by'], ['users.id'], name=op.f('fk_invites_used_by')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invites')),
    sa.UniqueConstraint('code_hash', name=op.f('uq_invites_code_hash')),
    sa.UniqueConstraint('used_by', name=op.f('uq_invites_used_by'))
    )
    op.create_table('pairing_codes',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('code_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("code_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_pairing_codes_code_hash_format')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_pairing_codes_expires_after_created')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_pairing_codes_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pairing_codes')),
    sa.UniqueConstraint('code_hash', name=op.f('uq_pairing_codes_code_hash'))
    )
    op.create_index('ix_pairing_codes_user_id', 'pairing_codes', ['user_id'], unique=False)
    op.create_table('web_sessions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_web_sessions_token_hash_format')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('ck_web_sessions_expires_after_created')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_web_sessions_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_web_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_web_sessions_token_hash'))
    )
    op.create_index('ix_web_sessions_user_id', 'web_sessions', ['user_id'], unique=False)
    op.create_table('access_tokens',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('device_id', sa.BigInteger(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_access_tokens_token_hash_format')),
    sa.CheckConstraint('expires_at > issued_at', name=op.f('ck_access_tokens_expires_after_issued')),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_access_tokens_device_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_access_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_access_tokens_token_hash'))
    )
    op.create_index('ix_access_tokens_device_id', 'access_tokens', ['device_id'], unique=False)
    op.create_table('refresh_tokens',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('device_id', sa.BigInteger(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_refresh_tokens_token_hash_format')),
    sa.CheckConstraint('expires_at > issued_at', name=op.f('ck_refresh_tokens_expires_after_issued')),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_refresh_tokens_device_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_refresh_tokens_token_hash'))
    )
    op.create_index('ix_refresh_tokens_device_id', 'refresh_tokens', ['device_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_refresh_tokens_device_id', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_index('ix_access_tokens_device_id', table_name='access_tokens')
    op.drop_table('access_tokens')
    op.drop_index('ix_web_sessions_user_id', table_name='web_sessions')
    op.drop_table('web_sessions')
    op.drop_index('ix_pairing_codes_user_id', table_name='pairing_codes')
    op.drop_table('pairing_codes')
    op.drop_table('invites')
    op.drop_index('ix_devices_user_id', table_name='devices')
    op.drop_table('devices')
    op.drop_index('ix_auth_failures_kind_subject_created_at', table_name='auth_failures')
    op.drop_index('ix_auth_failures_kind_ip_created_at', table_name='auth_failures')
    op.drop_table('auth_failures')
