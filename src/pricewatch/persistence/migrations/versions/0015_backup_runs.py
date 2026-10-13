"""Журнал резервных копий (backup_runs), DEVELOPMENT_ARCHITECTURE.md, раздел 11.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0015'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('backup_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('file_name', sa.Text(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('running', 'ok', 'failed')", name=op.f('ck_backup_runs_status')),
    sa.CheckConstraint("(status = 'running') = (finished_at IS NULL)", name=op.f('ck_backup_runs_finished_at_with_status')),
    sa.CheckConstraint("status <> 'ok' OR (file_name IS NOT NULL AND size_bytes IS NOT NULL)", name=op.f('ck_backup_runs_ok_with_file')),
    sa.CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name=op.f('ck_backup_runs_error_code_with_failure')),
    sa.CheckConstraint('file_name IS NULL OR char_length(file_name) BETWEEN 1 AND 200', name=op.f('ck_backup_runs_file_name_length')),
    sa.CheckConstraint('size_bytes IS NULL OR size_bytes >= 0', name=op.f('ck_backup_runs_size_bytes')),
    sa.CheckConstraint("error_code IS NULL OR error_code ~ '^[A-Z_]{1,60}$'", name=op.f('ck_backup_runs_error_code_format')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_backup_runs'))
    )
    op.create_index('ix_backup_runs_started_at', 'backup_runs', ['started_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_backup_runs_started_at', table_name='backup_runs')
    op.drop_table('backup_runs')
