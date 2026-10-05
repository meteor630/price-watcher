"""Коды связки, устройства и токены приложения на PostgreSQL. Правила — в pricewatch.application.ports."""

from datetime import datetime

from sqlalchemy import Connection, Row, Table, select, update

from pricewatch.application.ports import AccessGrant, PairingClaim, RefreshRecord
from pricewatch.domain.accounts import Device
from pricewatch.persistence.models import AccessTokenRow, DeviceRow, PairingCodeRow, RefreshTokenRow

pairing_codes: Table = PairingCodeRow.__table__
devices: Table = DeviceRow.__table__
access_tokens: Table = AccessTokenRow.__table__
refresh_tokens: Table = RefreshTokenRow.__table__


class SqlPairingCodeRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create(self, user_id: int, code_hash: str, created_at: datetime, expires_at: datetime) -> None:
        self.connection.execute(
            pairing_codes.insert().values(
                user_id=user_id, code_hash=code_hash, created_at=created_at, expires_at=expires_at
            )
        )

    def claim(self, code_hash: str) -> PairingClaim | None:
        row = self.connection.execute(
            select(pairing_codes).where(pairing_codes.c.code_hash == code_hash).with_for_update()
        ).one_or_none()
        if row is None:
            return None
        return PairingClaim(id=row.id, user_id=row.user_id, expires_at=row.expires_at, used=row.used_at is not None)

    def mark_used(self, code_id: int, now: datetime) -> None:
        self.connection.execute(update(pairing_codes).where(pairing_codes.c.id == code_id).values(used_at=now))


def _to_device(row: Row) -> Device:
    return Device(id=row.id, user_id=row.user_id, name=row.name, created_at=row.created_at)


class SqlDeviceRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create(self, user_id: int, name: str, created_at: datetime) -> Device:
        row = self.connection.execute(
            devices.insert()
            .values(user_id=user_id, name=name, created_at=created_at, last_seen_at=created_at)
            .returning(devices)
        ).one()
        return _to_device(row)

    def get_active(self, device_id: int) -> Device | None:
        row = self.connection.execute(
            select(devices).where(devices.c.id == device_id, devices.c.revoked_at.is_(None))
        ).one_or_none()
        return None if row is None else _to_device(row)

    def touch(self, device_id: int, now: datetime) -> None:
        self.connection.execute(update(devices).where(devices.c.id == device_id).values(last_seen_at=now))

    def revoke(self, device_id: int, now: datetime) -> None:
        self.connection.execute(
            update(devices).where(devices.c.id == device_id, devices.c.revoked_at.is_(None)).values(revoked_at=now)
        )


class SqlTokenRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def add_access(self, device_id: int, token_hash: str, issued_at: datetime, expires_at: datetime) -> None:
        self.connection.execute(
            access_tokens.insert().values(
                device_id=device_id, token_hash=token_hash, issued_at=issued_at, expires_at=expires_at
            )
        )

    def add_refresh(self, device_id: int, token_hash: str, issued_at: datetime, expires_at: datetime) -> None:
        self.connection.execute(
            refresh_tokens.insert().values(
                device_id=device_id, token_hash=token_hash, issued_at=issued_at, expires_at=expires_at
            )
        )

    def find_access(self, token_hash: str, now: datetime) -> AccessGrant | None:
        row = self.connection.execute(
            select(access_tokens.c.device_id, devices.c.user_id)
            .join(devices, devices.c.id == access_tokens.c.device_id)
            .where(
                access_tokens.c.token_hash == token_hash,
                access_tokens.c.expires_at > now,
                devices.c.revoked_at.is_(None),
            )
        ).one_or_none()
        return None if row is None else AccessGrant(device_id=row.device_id, user_id=row.user_id)

    def find_refresh(self, token_hash: str) -> RefreshRecord | None:
        row = self.connection.execute(
            select(refresh_tokens).where(refresh_tokens.c.token_hash == token_hash).with_for_update()
        ).one_or_none()
        if row is None:
            return None
        return RefreshRecord(id=row.id, device_id=row.device_id, used_at=row.used_at, expires_at=row.expires_at)

    def expires_of_access(self, token_hash: str) -> datetime | None:
        return self.connection.execute(
            select(access_tokens.c.expires_at).where(access_tokens.c.token_hash == token_hash)
        ).scalar_one_or_none()

    def expires_of_refresh(self, token_hash: str) -> datetime | None:
        return self.connection.execute(
            select(refresh_tokens.c.expires_at).where(refresh_tokens.c.token_hash == token_hash)
        ).scalar_one_or_none()

    def mark_refresh_used(self, refresh_id: int, now: datetime) -> None:
        self.connection.execute(update(refresh_tokens).where(refresh_tokens.c.id == refresh_id).values(used_at=now))
