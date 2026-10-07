"""Решения пользователей по подозрительным результатам проверки (price_decisions).

Каждый решает только за свои товары (решение владельца 07.10.2026): решение одного пользователя
не меняет цену у других.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('price_decisions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('observation_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('decision', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("decision IN ('accept', 'reject')", name=op.f('ck_price_decisions_decision')),
    sa.ForeignKeyConstraint(['observation_id'], ['price_observations.id'], name=op.f('fk_price_decisions_observation_id')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_price_decisions_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_price_decisions')),
    sa.UniqueConstraint('observation_id', 'user_id', name=op.f('uq_price_decisions_observation_id_user_id'))
    )
    op.create_index('ix_price_decisions_user_id', 'price_decisions', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_price_decisions_user_id', table_name='price_decisions')
    op.drop_table('price_decisions')
