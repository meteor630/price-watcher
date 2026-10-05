"""Пользователи и личный список покупок: users, categories, groups, items.

Revision ID: 0001
Revises: —
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('login', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('telegram_chat_id', sa.BigInteger(), nullable=True),
    sa.Column('categories_order_version', sa.BigInteger(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("role IN ('user', 'admin')", name=op.f('ck_users_role')),
    sa.CheckConstraint('categories_order_version >= 1', name=op.f('ck_users_categories_order_version')),
    sa.CheckConstraint('char_length(display_name) BETWEEN 1 AND 100', name=op.f('ck_users_display_name_length')),
    sa.CheckConstraint('char_length(login) BETWEEN 3 AND 64', name=op.f('ck_users_login_length')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('telegram_chat_id', name=op.f('uq_users_telegram_chat_id'))
    )
    op.create_index('uq_users_login_lower', 'users', [sa.literal_column('lower(login)')], unique=True)
    op.create_table('categories',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('name_normalized', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("name_normalized <> ''", name=op.f('ck_categories_name_normalized')),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 100', name=op.f('ck_categories_name_length')),
    sa.CheckConstraint('position >= 0', name=op.f('ck_categories_position')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_categories_version')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_categories_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_categories')),
    sa.UniqueConstraint('id', 'user_id', name=op.f('uq_categories_id_user_id'))
    )
    op.create_index('ix_categories_user_id_position', 'categories', ['user_id', 'position'], unique=False)
    op.create_index('uq_categories_user_id_name_active', 'categories', ['user_id', 'name_normalized'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('groups',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('name_normalized', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("name_normalized <> ''", name=op.f('ck_groups_name_normalized')),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 100', name=op.f('ck_groups_name_length')),
    sa.CheckConstraint('position >= 0', name=op.f('ck_groups_position')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_groups_version')),
    sa.ForeignKeyConstraint(['category_id', 'user_id'], ['categories.id', 'categories.user_id'], name=op.f('fk_groups_category_id_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_groups')),
    sa.UniqueConstraint('id', 'category_id', 'user_id', name=op.f('uq_groups_id_category_id_user_id'))
    )
    op.create_index('ix_groups_category_id_position', 'groups', ['category_id', 'position'], unique=False)
    op.create_index('uq_groups_category_id_name_active', 'groups', ['category_id', 'name_normalized'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('items',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=False),
    sa.Column('group_id', sa.BigInteger(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('full_name', sa.Text(), nullable=True),
    sa.Column('quantity', sa.Integer(), server_default='1', nullable=False),
    sa.Column('in_total', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('version', sa.BigInteger(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 200', name=op.f('ck_items_name_length')),
    sa.CheckConstraint('full_name IS NULL OR char_length(full_name) BETWEEN 1 AND 500', name=op.f('ck_items_full_name_length')),
    sa.CheckConstraint('position >= 0', name=op.f('ck_items_position')),
    sa.CheckConstraint('quantity BETWEEN 1 AND 999', name=op.f('ck_items_quantity')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_items_version')),
    sa.ForeignKeyConstraint(['category_id', 'user_id'], ['categories.id', 'categories.user_id'], name=op.f('fk_items_category_id_user_id')),
    sa.ForeignKeyConstraint(['group_id', 'category_id', 'user_id'], ['groups.id', 'groups.category_id', 'groups.user_id'], name=op.f('fk_items_group_id_category_id_user_id')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_items'))
    )
    op.create_index('ix_items_category_id_group_id_position', 'items', ['category_id', 'group_id', 'position'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_items_category_id_group_id_position', table_name='items')
    op.drop_table('items')
    op.drop_index('uq_groups_category_id_name_active', table_name='groups', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_groups_category_id_position', table_name='groups')
    op.drop_table('groups')
    op.drop_index('uq_categories_user_id_name_active', table_name='categories', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_categories_user_id_position', table_name='categories')
    op.drop_table('categories')
    op.drop_index('uq_users_login_lower', table_name='users')
    op.drop_table('users')
