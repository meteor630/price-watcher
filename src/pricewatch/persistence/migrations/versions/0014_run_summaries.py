"""Сводка проверки пользователям: когда по завершённому запуску созданы сообщения (parse_runs.summarized_at).

Уже завершённые запуски отмечаются сразу: после обновления старые проверки сводок не рассылают.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('parse_runs', sa.Column('summarized_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE parse_runs SET summarized_at = finished_at WHERE finished_at IS NOT NULL")
    op.create_check_constraint(op.f('ck_parse_runs_summarized_after_finish'), 'parse_runs', 'summarized_at IS NULL OR finished_at IS NOT NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_parse_runs_summarized_after_finish'), 'parse_runs', type_='check')
    op.drop_column('parse_runs', 'summarized_at')
