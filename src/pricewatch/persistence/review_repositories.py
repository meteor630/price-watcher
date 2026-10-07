"""Решения пользователей по подозрительным наблюдениям и журнал действий."""

from datetime import datetime

from sqlalchemy import Connection, Table, delete, insert, select
from sqlalchemy.exc import IntegrityError

from pricewatch.application.ports import AuditEvent
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.review import Decision
from pricewatch.persistence.models import AuditEventRow, PriceDecisionRow

decisions: Table = PriceDecisionRow.__table__
audit_events: Table = AuditEventRow.__table__


class SqlDecisionRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def add(self, user_id: int, observation_id: int, decision: Decision, now: datetime) -> int:
        savepoint = self.connection.begin_nested()
        try:
            decision_id = self.connection.execute(
                insert(decisions).values(observation_id=observation_id, user_id=user_id, decision=decision.value, created_at=now).returning(decisions.c.id)
            ).scalar_one()
        except IntegrityError as error:
            # Второе нажатие или вторая вкладка: решение по этому наблюдению уже есть.
            savepoint.rollback()
            raise PriceWatcherError(ErrorCode.VERSION_CONFLICT) from error
        savepoint.commit()
        return decision_id

    def delete(self, user_id: int, decision_id: int) -> tuple[int, Decision] | None:
        row = self.connection.execute(
            delete(decisions).where(decisions.c.id == decision_id, decisions.c.user_id == user_id)
            .returning(decisions.c.observation_id, decisions.c.decision)
        ).one_or_none()
        return None if row is None else (row.observation_id, Decision(row.decision))


class SqlAuditRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def add(self, event: AuditEvent) -> None:
        self.connection.execute(
            insert(audit_events).values(
                actor_id=event.actor_id, action=event.action, entity_type=event.entity_type, entity_id=event.entity_id,
                before_json=event.before, after_json=event.after, created_at=event.at,
            )
        )

    def last(self, action: str, entity_type: str, entity_id: int) -> AuditEvent | None:
        row = self.connection.execute(
            select(audit_events)
            .where(audit_events.c.action == action, audit_events.c.entity_type == entity_type, audit_events.c.entity_id == entity_id)
            .order_by(audit_events.c.created_at.desc(), audit_events.c.id.desc())
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        return AuditEvent(row.actor_id, row.action, row.entity_type, row.entity_id, row.before_json, row.after_json, row.created_at)

    def recent(self, actor_id: int, action: str, since: datetime) -> list[datetime]:
        rows = self.connection.execute(
            select(audit_events.c.created_at)
            .where(audit_events.c.actor_id == actor_id, audit_events.c.action == action, audit_events.c.created_at >= since)
            .order_by(audit_events.c.created_at)
        )
        return [row.created_at for row in rows]
