"""Профиль seccomp для контейнера worker: стандартный профиль Docker плюс песочница Chrome.

Стандартный профиль (moby/profiles, Apache-2.0) запрещает без CAP_SYS_ADMIN создавать пространства имён
процессов и сети, а песочница Chrome создаёт их вместе с пространством пользователя. Добавляется одно
правило — clone и unshare; остальное как у Docker (решение владельца 12.10.2026).

Пересобрать после обновления Docker:
    uv run python tools/chrome_seccomp.py <moby default.json> > deploy/home/chrome-seccomp.json
Источник, по которому собран текущий файл: https://github.com/moby/profiles/blob/<SOURCE_COMMIT>/seccomp/default.json
"""

import json
import sys
from pathlib import Path
from typing import Any

SOURCE_COMMIT = "6fe7deb1b9fb7c0397a4593480d7d22b9ee8caef"
CHROME_RULE: dict[str, Any] = {
    "names": ["clone", "unshare"],
    "action": "SCMP_ACT_ALLOW",
    "comment": f"Песочница Chrome: пространства имён пользователя, процессов и сети без CAP_SYS_ADMIN. "
               f"Остальное — стандартный профиль Docker, moby/profiles {SOURCE_COMMIT[:7]}, Apache-2.0.",
}


def chrome_profile(default: dict[str, Any]) -> dict[str, Any]:
    """Стандартный профиль с правилом для Chrome; профиль, где правило уже есть, — ошибка (не тот файл на входе)."""
    if CHROME_RULE in default["syscalls"]:
        raise ValueError("правило Chrome уже есть: на входе должен быть стандартный профиль Docker")
    return {**default, "syscalls": [*default["syscalls"], CHROME_RULE]}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Использование: python tools/chrome_seccomp.py <moby default.json>", file=sys.stderr)
        return 2
    default = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    try:
        profile = chrome_profile(default)
    except ValueError as error:
        print(f"Профиль не собран: {error}", file=sys.stderr)
        return 1
    # Вывод — UTF-8 независимо от кодировки системы (Windows пишет в канал в cp1252).
    sys.stdout.buffer.write((json.dumps(profile, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
