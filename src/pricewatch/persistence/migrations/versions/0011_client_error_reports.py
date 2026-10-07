"""Отчёты об ошибках приложения (client_error_reports), DEVELOPMENT_ARCHITECTURE.md, раздел 10.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('client_error_reports',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('error_id', sa.Text(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('device_id', sa.BigInteger(), nullable=False),
    sa.Column('app_version', sa.Text(), nullable=False),
    sa.Column('os_version', sa.Text(), nullable=False),
    sa.Column('screen', sa.Text(), nullable=False),
    sa.Column('error_code', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('char_length(app_version) <= 40', name=op.f('ck_client_error_reports_app_version_length')),
    sa.CheckConstraint('char_length(os_version) <= 40', name=op.f('ck_client_error_reports_os_version_length')),
    sa.CheckConstraint('char_length(screen) <= 80', name=op.f('ck_client_error_reports_screen_length')),
    sa.CheckConstraint('char_length(error_code) <= 80', name=op.f('ck_client_error_reports_error_code_length')),
    sa.CheckConstraint('char_length(message) <= 2000', name=op.f('ck_client_error_reports_message_length')),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_client_error_reports_device_id')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_client_error_reports_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_client_error_reports')),
    sa.UniqueConstraint('error_id', name=op.f('uq_client_error_reports_error_id'))
    )
    op.create_index('ix_client_error_reports_device_id_received_at', 'client_error_reports', ['device_id', 'received_at'], unique=False)
    op.create_index('ix_client_error_reports_received_at', 'client_error_reports', ['received_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_client_error_reports_received_at', table_name='client_error_reports')
    op.drop_index('ix_client_error_reports_device_id_received_at', table_name='client_error_reports')
    op.drop_table('client_error_reports')
