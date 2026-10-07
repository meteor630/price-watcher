"""Товар: данные покупки и момент переноса в архив (DEVELOPMENT_ARCHITECTURE.md, разделы 5 и 6).

`purchase_baseline_minor` — лучшая цена за штуку в момент добавления товара, запоминается при покупке:
экономия не пересчитывается задним числом и не требует читать историю цен (решение владельца 07.10.2026).

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS = {
    'purchase_with_state': "(state = 'purchased') = (purchase_price_minor IS NOT NULL AND purchase_quantity IS NOT NULL)",
    'purchase_only_when_purchased': (
        "state = 'purchased' OR (purchase_price_minor IS NULL AND purchase_quantity IS NULL "
        "AND purchase_store_id IS NULL AND purchase_baseline_minor IS NULL)"
    ),
    'purchase_price': 'purchase_price_minor BETWEEN 1 AND 1000000000000',
    'purchase_quantity': 'purchase_quantity BETWEEN 1 AND 999',
    'purchase_baseline': 'purchase_baseline_minor > 0',
    'archived_at_with_state': "(state = 'archived') = (archived_at IS NOT NULL)",
}


def upgrade() -> None:
    op.add_column('items', sa.Column('purchase_price_minor', sa.BigInteger(), nullable=True))
    op.add_column('items', sa.Column('purchase_quantity', sa.Integer(), nullable=True))
    op.add_column('items', sa.Column('purchase_store_id', sa.BigInteger(), nullable=True))
    op.add_column('items', sa.Column('purchase_baseline_minor', sa.BigInteger(), nullable=True))
    op.add_column('items', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(op.f('fk_items_purchase_store_id_stores'), 'items', 'stores', ['purchase_store_id'], ['id'])
    # Архивных товаров без даты до этой миграции создать было нельзя, кроме как напрямую в базе;
    # для них момент архива — последнее изменение записи.
    op.execute("UPDATE items SET archived_at = updated_at WHERE state = 'archived'")
    for name, condition in CHECKS.items():
        op.create_check_constraint(op.f(f'ck_items_{name}'), 'items', condition)


def downgrade() -> None:
    for name in reversed(CHECKS):
        op.drop_constraint(op.f(f'ck_items_{name}'), 'items', type_='check')
    op.drop_constraint(op.f('fk_items_purchase_store_id_stores'), 'items', type_='foreignkey')
    op.drop_column('items', 'archived_at')
    op.drop_column('items', 'purchase_baseline_minor')
    op.drop_column('items', 'purchase_store_id')
    op.drop_column('items', 'purchase_quantity')
    op.drop_column('items', 'purchase_price_minor')
