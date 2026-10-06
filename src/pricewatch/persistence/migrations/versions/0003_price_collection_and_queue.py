"""Сбор цен и очередь: магазины, ссылки, привязки к товарам, наблюдения, запуски, задания, worker, настройки;
состояние товара и дата покупки.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Сначала товары: на их составной ключ (id, user_id) ссылается item_links.
    op.add_column('items', sa.Column('state', sa.Text(), server_default='active', nullable=False))
    op.add_column('items', sa.Column('purchased_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(op.f('ck_items_state'), 'items', "state IN ('active', 'purchased', 'archived')")
    op.create_check_constraint(op.f('ck_items_purchased_at_with_state'), 'items', "(state = 'purchased') = (purchased_at IS NOT NULL)")
    op.create_unique_constraint(op.f('uq_items_id_user_id'), 'items', ['id', 'user_id'])
    op.create_table('stores',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('canonical_host', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('color', sa.Text(), nullable=False),
    sa.Column('has_card_price', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('parser_key', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('auto_created', sa.Boolean(), nullable=False),
    sa.Column('fetch_method', sa.Text(), nullable=True),
    sa.Column('min_delay_ms', sa.Integer(), nullable=False),
    sa.Column('paused_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pause_level', sa.Integer(), server_default='0', nullable=False),
    sa.Column('pause_reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("canonical_host ~ '^[a-z0-9.-]{1,253}$'", name=op.f('ck_stores_canonical_host_format')),
    sa.CheckConstraint("color ~ '^store-[1-8]$'", name=op.f('ck_stores_color')),
    sa.CheckConstraint("fetch_method IS NULL OR fetch_method IN ('http', 'browser')", name=op.f('ck_stores_fetch_method')),
    sa.CheckConstraint("parser_key ~ '^[a-z_]{1,40}$'", name=op.f('ck_stores_parser_key_format')),
    sa.CheckConstraint("pause_reason IS NULL OR pause_reason IN ('BLOCKED', 'CAPTCHA')", name=op.f('ck_stores_pause_reason')),
    sa.CheckConstraint('(paused_until IS NULL) = (pause_reason IS NULL)', name=op.f('ck_stores_pause_together')),
    sa.CheckConstraint('char_length(display_name) BETWEEN 1 AND 100', name=op.f('ck_stores_display_name_length')),
    sa.CheckConstraint('min_delay_ms BETWEEN 0 AND 600000', name=op.f('ck_stores_min_delay_ms')),
    sa.CheckConstraint('pause_level BETWEEN 0 AND 10', name=op.f('ck_stores_pause_level')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stores'))
    )
    op.create_index('uq_stores_canonical_host', 'stores', ['canonical_host'], unique=True)
    op.create_table('workers',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('next_token_hash', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("next_token_hash IS NULL OR next_token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_workers_next_token_hash_format')),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_workers_token_hash_format')),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 80', name=op.f('ck_workers_name_length')),
    sa.CheckConstraint('next_token_hash IS NULL OR next_token_hash <> token_hash', name=op.f('ck_workers_tokens_differ')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workers')),
    sa.UniqueConstraint('name', name=op.f('uq_workers_name')),
    sa.UniqueConstraint('next_token_hash', name=op.f('uq_workers_next_token_hash')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_workers_token_hash'))
    )
    op.create_table('parse_runs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('slot_key', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('requested_by', sa.BigInteger(), nullable=True),
    sa.Column('total', sa.Integer(), server_default='0', nullable=False),
    sa.Column('done', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(kind = 'scheduled') = (slot_key IS NOT NULL)", name=op.f('ck_parse_runs_slot_key_with_kind')),
    sa.CheckConstraint("(status IN ('finished', 'failed')) = (finished_at IS NOT NULL)", name=op.f('ck_parse_runs_finished_at_with_status')),
    sa.CheckConstraint("kind <> 'manual' OR requested_by IS NOT NULL", name=op.f('ck_parse_runs_manual_requested_by')),
    sa.CheckConstraint("kind IN ('scheduled', 'manual', 'on_add')", name=op.f('ck_parse_runs_kind')),
    sa.CheckConstraint("status IN ('queued', 'running', 'stopping', 'finished', 'failed')", name=op.f('ck_parse_runs_status')),
    sa.CheckConstraint('total >= 0 AND done >= 0 AND failed >= 0 AND done + failed <= total', name=op.f('ck_parse_runs_counters')),
    sa.ForeignKeyConstraint(['requested_by'], ['users.id'], name=op.f('fk_parse_runs_requested_by')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parse_runs'))
    )
    op.create_index('uq_parse_runs_one_active', 'parse_runs', [sa.literal_column('(true)')], unique=True, postgresql_where=sa.text("kind IN ('scheduled', 'manual') AND status IN ('queued', 'running', 'stopping')"))
    op.create_index('uq_parse_runs_slot_key', 'parse_runs', ['slot_key'], unique=True, postgresql_where=sa.text('slot_key IS NOT NULL'))
    op.create_table('settings',
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('value_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default='1', nullable=False),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("key ~ '^[a-z_]{1,60}$'", name=op.f('ck_settings_key_format')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_settings_version')),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name=op.f('fk_settings_updated_by')),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_settings'))
    )
    op.create_table('tracked_links',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('store_id', sa.BigInteger(), nullable=False),
    sa.Column('normalized_url', sa.Text(), nullable=False),
    sa.Column('region_code', sa.Text(), nullable=False),
    sa.Column('baseline_title', sa.Text(), nullable=True),
    sa.Column('image_path', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("normalized_url LIKE 'https://%%' AND char_length(normalized_url) <= 2048", name=op.f('ck_tracked_links_normalized_url')),
    sa.CheckConstraint("region_code ~ '^[a-z]{2,10}$'", name=op.f('ck_tracked_links_region_code')),
    sa.CheckConstraint('baseline_title IS NULL OR char_length(baseline_title) BETWEEN 1 AND 500', name=op.f('ck_tracked_links_baseline_title_length')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_tracked_links_store_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tracked_links'))
    )
    op.create_index('ix_tracked_links_store_id', 'tracked_links', ['store_id'], unique=False)
    op.create_index('uq_tracked_links_normalized_url_region_code', 'tracked_links', ['normalized_url', 'region_code'], unique=True)
    op.create_table('check_tasks',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('tracked_link_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Text(), server_default='queued', nullable=False),
    sa.Column('priority', sa.Integer(), server_default='0', nullable=False),
    sa.Column('attempt', sa.Integer(), server_default='0', nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('worker_id', sa.BigInteger(), nullable=True),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'leased') = (lease_until IS NOT NULL AND worker_id IS NOT NULL)", name=op.f('ck_check_tasks_lease_with_status')),
    sa.CheckConstraint("(status IN ('done', 'failed', 'cancelled')) = (finished_at IS NOT NULL)", name=op.f('ck_check_tasks_finished_at_with_status')),
    sa.CheckConstraint("error_code IS NULL OR error_code IN ('ok', 'INVALID_URL', 'STORE_NOT_ALLOWED', 'BLOCKED', 'CAPTCHA', 'TIMEOUT', 'LAYOUT_CHANGED', 'PRICE_NOT_FOUND', 'REGION_MISMATCH', 'UNSUPPORTED_CURRENCY', 'INTERNAL_ERROR')", name=op.f('ck_check_tasks_error_code')),
    sa.CheckConstraint("status IN ('queued', 'leased', 'done', 'failed', 'cancelled')", name=op.f('ck_check_tasks_status')),
    sa.CheckConstraint('attempt BETWEEN 0 AND 3', name=op.f('ck_check_tasks_attempt')),
    sa.CheckConstraint('duration_ms IS NULL OR duration_ms >= 0', name=op.f('ck_check_tasks_duration_ms')),
    sa.ForeignKeyConstraint(['run_id'], ['parse_runs.id'], name=op.f('fk_check_tasks_run_id')),
    sa.ForeignKeyConstraint(['tracked_link_id'], ['tracked_links.id'], name=op.f('fk_check_tasks_tracked_link_id')),
    sa.ForeignKeyConstraint(['worker_id'], ['workers.id'], name=op.f('fk_check_tasks_worker_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_check_tasks'))
    )
    op.create_index('ix_check_tasks_run_id', 'check_tasks', ['run_id'], unique=False)
    op.create_index('ix_check_tasks_status_priority_id', 'check_tasks', ['status', 'priority', 'id'], unique=False)
    op.create_index('uq_check_tasks_tracked_link_id_active', 'check_tasks', ['tracked_link_id'], unique=True, postgresql_where=sa.text("status IN ('queued', 'leased')"))
    op.create_table('price_observations',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('tracked_link_id', sa.BigInteger(), nullable=False),
    sa.Column('task_id', sa.BigInteger(), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('price_minor', sa.BigInteger(), nullable=True),
    sa.Column('card_price_minor', sa.BigInteger(), nullable=True),
    sa.Column('availability', sa.Text(), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('region_code', sa.Text(), nullable=False),
    sa.Column('confirmation', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=True),
    sa.CheckConstraint("availability IN ('in_stock', 'out_of_stock', 'unknown')", name=op.f('ck_price_observations_availability')),
    sa.CheckConstraint("confirmation IN ('confirmed', 'pending', 'rejected')", name=op.f('ck_price_observations_confirmation')),
    sa.CheckConstraint("source IS NULL OR source IN ('site_data', 'page')", name=op.f('ck_price_observations_source')),
    sa.CheckConstraint("status <> 'ok' OR availability = 'out_of_stock' OR price_minor IS NOT NULL", name=op.f('ck_price_observations_success_with_price')),
    sa.CheckConstraint("status = 'ok' OR (price_minor IS NULL AND card_price_minor IS NULL AND confirmation = 'confirmed' AND source IS NULL)", name=op.f('ck_price_observations_failure_without_price')),
    sa.CheckConstraint("status IN ('ok', 'INVALID_URL', 'STORE_NOT_ALLOWED', 'BLOCKED', 'CAPTCHA', 'TIMEOUT', 'LAYOUT_CHANGED', 'PRICE_NOT_FOUND', 'REGION_MISMATCH', 'UNSUPPORTED_CURRENCY', 'INTERNAL_ERROR')", name=op.f('ck_price_observations_status')),
    sa.CheckConstraint('card_price_minor IS NULL OR card_price_minor > 0', name=op.f('ck_price_observations_card_price_minor')),
    sa.CheckConstraint('price_minor IS NULL OR price_minor > 0', name=op.f('ck_price_observations_price_minor')),
    sa.CheckConstraint('title IS NULL OR char_length(title) BETWEEN 1 AND 500', name=op.f('ck_price_observations_title_length')),
    sa.ForeignKeyConstraint(['task_id'], ['check_tasks.id'], name=op.f('fk_price_observations_task_id')),
    sa.ForeignKeyConstraint(['tracked_link_id'], ['tracked_links.id'], name=op.f('fk_price_observations_tracked_link_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_price_observations'))
    )
    op.create_index('ix_price_observations_tracked_link_id_checked_at', 'price_observations', ['tracked_link_id', 'checked_at'], unique=False)
    op.create_index('uq_price_observations_task_id', 'price_observations', ['task_id'], unique=True)
    op.create_table('item_links',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('item_id', sa.BigInteger(), nullable=False),
    sa.Column('tracked_link_id', sa.BigInteger(), nullable=False),
    sa.Column('store_id', sa.BigInteger(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name=op.f('ck_item_links_valid_range')),
    sa.ForeignKeyConstraint(['item_id', 'user_id'], ['items.id', 'items.user_id'], name=op.f('fk_item_links_item_id_user_id')),
    sa.ForeignKeyConstraint(['store_id'], ['stores.id'], name=op.f('fk_item_links_store_id')),
    sa.ForeignKeyConstraint(['tracked_link_id'], ['tracked_links.id'], name=op.f('fk_item_links_tracked_link_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_item_links'))
    )
    op.create_index('ix_item_links_tracked_link_id', 'item_links', ['tracked_link_id'], unique=False)
    op.create_index('uq_item_links_item_id_store_id_active', 'item_links', ['item_id', 'store_id'], unique=True, postgresql_where=sa.text('valid_to IS NULL'))


def downgrade() -> None:
    op.drop_index('uq_item_links_item_id_store_id_active', table_name='item_links', postgresql_where=sa.text('valid_to IS NULL'))
    op.drop_index('ix_item_links_tracked_link_id', table_name='item_links')
    op.drop_table('item_links')
    op.drop_index('uq_price_observations_task_id', table_name='price_observations')
    op.drop_index('ix_price_observations_tracked_link_id_checked_at', table_name='price_observations')
    op.drop_table('price_observations')
    op.drop_index('uq_check_tasks_tracked_link_id_active', table_name='check_tasks', postgresql_where=sa.text("status IN ('queued', 'leased')"))
    op.drop_index('ix_check_tasks_status_priority_id', table_name='check_tasks')
    op.drop_index('ix_check_tasks_run_id', table_name='check_tasks')
    op.drop_table('check_tasks')
    op.drop_index('uq_tracked_links_normalized_url_region_code', table_name='tracked_links')
    op.drop_index('ix_tracked_links_store_id', table_name='tracked_links')
    op.drop_table('tracked_links')
    op.drop_table('settings')
    op.drop_index('uq_parse_runs_slot_key', table_name='parse_runs', postgresql_where=sa.text('slot_key IS NOT NULL'))
    op.drop_index('uq_parse_runs_one_active', table_name='parse_runs', postgresql_where=sa.text("kind IN ('scheduled', 'manual') AND status IN ('queued', 'running', 'stopping')"))
    op.drop_table('parse_runs')
    op.drop_table('workers')
    op.drop_index('uq_stores_canonical_host', table_name='stores')
    op.drop_table('stores')
    # Товары — последними: их ключ (id, user_id) нужен item_links, пока она существует.
    op.drop_constraint(op.f('uq_items_id_user_id'), 'items', type_='unique')
    op.drop_constraint(op.f('ck_items_purchased_at_with_state'), 'items', type_='check')
    op.drop_constraint(op.f('ck_items_state'), 'items', type_='check')
    op.drop_column('items', 'purchased_at')
    op.drop_column('items', 'state')
