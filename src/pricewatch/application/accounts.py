"""Сценарии учётных записей."""

from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.ports import UnitOfWork
from pricewatch.domain.accounts import Role, User, check_display_name, check_login, check_password


def create_admin(uow: UnitOfWork, hasher: PasswordHasher, login: str, password: str, display_name: str) -> User:
    """Первый и следующие администраторы создаются командой на сервере (раздел 4 архитектуры)."""
    clean_login = check_login(login)
    clean_password = check_password(password, clean_login)
    name = check_display_name(display_name)
    with uow:
        user = uow.users.create(clean_login, hasher.hash(clean_password), Role.ADMIN, name)
        uow.commit()
    return user
