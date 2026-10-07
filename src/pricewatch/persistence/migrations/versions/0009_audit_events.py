"""Журнал действий (audit_events): решения по подозрительным изменениям, их автоматическое закрытие,
перепроверки, изменения настроек проверки.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('actor_id', sa.BigInteger(), nullable=True),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('entity_type', sa.Text(), nullable=False),
    sa.Column('entity_id', sa.BigInteger(), nullable=True),
    sa.Column('before_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action ~ '^[a-z_]{1,60}$'", name=op.f('ck_audit_events_action_format')),
    sa.CheckConstraint("entity_type ~ '^[a-z_]{1,60}$'", name=op.f('ck_audit_events_entity_type_format')),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_audit_events_actor_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events'))
    )
    op.create_index('ix_audit_events_actor_id_action_created_at', 'audit_events', ['actor_id', 'action', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_audit_events_actor_id_action_created_at', table_name='audit_events')
    op.drop_table('audit_events')
