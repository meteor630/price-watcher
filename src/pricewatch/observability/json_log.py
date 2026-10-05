"""Журнал: одна JSON-строка на событие в stdout.

Поля: ts, level, logger, message, request_id, error_id, остальные поля из `extra`
и exc — traceback. Всё проходит вырезание секретов. Если запись не удаётся
отформатировать, в журнал всё равно уходит строка с уровнем, источником и причиной.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from pricewatch.observability.ids import current_request_id
from pricewatch.observability.redaction import redact_mapping, redact_text

# Атрибуты LogRecord, которые не являются пользовательскими полями из `extra`.
_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        try:
            return self._format(record)
        except Exception as error:  # запись не должна теряться из-за ошибки форматирования
            fallback = {
                "ts": _timestamp(record),
                "level": record.levelname,
                "logger": record.name,
                "message": "Не удалось отформатировать запись журнала",
                "format_error": type(error).__name__,
                "request_id": current_request_id(),
            }
            return json.dumps(fallback, ensure_ascii=False)

    def _format(self, record: logging.LogRecord) -> str:
        extra = {key: value for key, value in vars(record).items() if key not in _RECORD_ATTRIBUTES}
        entry = redact_mapping(extra)
        # Основные поля важнее одноимённых полей из extra.
        entry.update(
            ts=_timestamp(record),
            level=record.levelname,
            logger=record.name,
            message=redact_text(record.getMessage()),
        )
        entry["request_id"] = current_request_id() or entry.get("request_id")
        if record.exc_info:
            entry["exc"] = redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            entry["stack"] = redact_text(self.formatStack(record.stack_info))
        return json.dumps(entry, ensure_ascii=False, default=lambda value: redact_text(repr(value)))


def configure_logging(level: str) -> None:
    """Корневой журнал пишет JSON в stdout. Повторный вызов заменяет обработчик, а не добавляет второй."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)



def use_utf8_streams() -> None:
    """Ввод и вывод процесса — UTF-8 независимо от системы; вызывается точкой входа процесса.

    Иначе кодировка берётся из системы (на Windows при записи в файл, службу
    или Docker — cp1252): журнал с кириллицей теряется с ошибкой, а пароль
    из --password-stdin читается искажённым.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def _timestamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
