"""Код DOMAIN_NOT_ALLOWED (домашний worker не пускает домен не из своего списка) в заданиях и наблюдениях;
неудачные попытки токена worker (auth_failures.kind = 'worker').

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("'ok', 'INVALID_URL', 'STORE_NOT_ALLOWED', 'BLOCKED', 'CAPTCHA', 'TIMEOUT', 'LAYOUT_CHANGED', "
       "'PRICE_NOT_FOUND', 'REGION_MISMATCH', 'UNSUPPORTED_CURRENCY', 'INTERNAL_ERROR'")
NEW = ("'ok', 'INVALID_URL', 'STORE_NOT_ALLOWED', 'DOMAIN_NOT_ALLOWED', 'BLOCKED', 'CAPTCHA', 'TIMEOUT', 'LAYOUT_CHANGED', "
       "'PRICE_NOT_FOUND', 'REGION_MISMATCH', 'UNSUPPORTED_CURRENCY', 'INTERNAL_ERROR'")


def _statuses(values: str) -> None:
    op.drop_constraint(op.f('ck_check_tasks_error_code'), 'check_tasks', type_='check')
    op.create_check_constraint(op.f('ck_check_tasks_error_code'), 'check_tasks', f"error_code IS NULL OR error_code IN ({values})")
    op.drop_constraint(op.f('ck_price_observations_status'), 'price_observations', type_='check')
    op.create_check_constraint(op.f('ck_price_observations_status'), 'price_observations', f"status IN ({values})")


def _failure_kinds(values: str) -> None:
    op.drop_constraint(op.f('ck_auth_failures_kind'), 'auth_failures', type_='check')
    op.create_check_constraint(op.f('ck_auth_failures_kind'), 'auth_failures', f"kind IN ({values})")


def upgrade() -> None:
    _statuses(NEW)
    _failure_kinds("'login', 'pairing', 'backup', 'worker'")


def downgrade() -> None:
    # Наблюдения и задания с новым кодом при откате становятся INTERNAL_ERROR: старая схема его не знает.
    op.execute("UPDATE price_observations SET status = 'INTERNAL_ERROR' WHERE status = 'DOMAIN_NOT_ALLOWED'")
    op.execute("UPDATE check_tasks SET error_code = 'INTERNAL_ERROR' WHERE error_code = 'DOMAIN_NOT_ALLOWED'")
    op.execute("DELETE FROM auth_failures WHERE kind = 'worker'")
    _statuses(OLD)
    _failure_kinds("'login', 'pairing', 'backup'")
