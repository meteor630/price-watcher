"""backup.pull: домашний приёмник забирает с сервера новые копии, сверяет размер и sha256, подтверждает получение,
дома хранит 30 суток; список от сервера разбирается строго; копии на сервере не удаляются никогда.

Сервер подменён MockTransport: он записывает каждый запрос, поэтому видно, что приёмник не шлёт DELETE и не лишнего."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from pricewatch.backup.pull import LIST_LIMIT, BackupPuller, PullError, parse_listing
from pricewatch.domain.backups import KEEP, backup_name

NOW = datetime(2026, 11, 28, 4, 0, tzinfo=UTC)
TOKEN = "reader-token-" + "r" * 40
NEW = backup_name(NOW - timedelta(hours=1))
YESTERDAY = backup_name(NOW - timedelta(days=1))
OLD = backup_name(NOW - KEEP - timedelta(hours=1))


class Server:
    """Копии сервера и журнал запросов; body_for позволяет испортить ответ на скачивание."""

    def __init__(self, files: dict[str, bytes], *, listing: bytes | None = None) -> None:
        self.files = files
        self.listing = listing
        self.requests: list[tuple[str, str]] = []
        self.body_for: dict[str, bytes] = {}
        self.broken: set[str] = set()

    def items(self) -> list[dict[str, object]]:
        return [{"name": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(self.files.items())]

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append((request.method, request.url.path))
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx2.Response(401)
        if request.method == "GET" and request.url.path == "/backups/v1/files":
            return httpx2.Response(200, content=self.listing if self.listing is not None else json.dumps({"items": self.items()}).encode())
        if request.method == "GET" and request.url.path.startswith("/backups/v1/files/"):
            name = request.url.path.rsplit("/", 1)[1]
            if name in self.broken:
                raise httpx2.ReadError("connection reset", request=request)
            return httpx2.Response(200, content=self.body_for.get(name, self.files[name]))
        if request.method == "POST" and request.url.path == "/backups/v1/received":
            return httpx2.Response(204)
        return httpx2.Response(404)


def puller(server: Server, folder: Path, max_bytes: int = 10_000) -> BackupPuller:
    client = httpx2.Client(base_url="https://prices.example.ru", transport=httpx2.MockTransport(server))
    return BackupPuller(client, TOKEN, folder, max_bytes, lambda: NOW)


def received_confirmations(server: Server) -> int:
    return server.requests.count(("POST", "/backups/v1/received"))


# --- обычный путь ---


def test_new_copies_are_downloaded_verified_and_confirmed(tmp_path: Path) -> None:
    server = Server({YESTERDAY: b"copy-1", NEW: b"copy-2"})
    report = puller(server, tmp_path).pull()
    assert sorted(report.received) == sorted([YESTERDAY, NEW]) and report.rejected == ()
    assert (tmp_path / NEW).read_bytes() == b"copy-2"
    assert (tmp_path / (NEW + ".sha256")).read_text(encoding="ascii").startswith(hashlib.sha256(b"copy-2").hexdigest())
    assert received_confirmations(server) == 2
    assert all(method in {"GET", "POST"} for method, _ in server.requests)


def test_copies_already_at_home_are_not_downloaded_again(tmp_path: Path) -> None:
    (tmp_path / YESTERDAY).write_bytes(b"copy-1")
    server = Server({YESTERDAY: b"copy-1", NEW: b"copy-2"})
    report = puller(server, tmp_path).pull()
    assert report.received == (NEW,)
    assert ("GET", f"/backups/v1/files/{YESTERDAY}") not in server.requests


def test_home_keeps_30_days_and_never_deletes_on_server(tmp_path: Path) -> None:
    (tmp_path / OLD).write_bytes(b"old")
    (tmp_path / (OLD + ".sha256")).write_text("x", encoding="ascii")
    (tmp_path / "notes.txt").write_text("mine")
    server = Server({NEW: b"copy-2"})
    report = puller(server, tmp_path).pull()
    assert report.removed == (OLD,)
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted([NEW, NEW + ".sha256", "notes.txt"])
    assert not [request for request in server.requests if request[0] not in {"GET", "POST"}]


# --- граница: размер ровно на лимите, остатки прошлой загрузки ---


def test_copy_exactly_at_limit_is_downloaded_and_bigger_is_skipped(tmp_path: Path) -> None:
    server = Server({YESTERDAY: b"x" * 100, NEW: b"y" * 101})
    report = puller(server, tmp_path, max_bytes=100).pull()
    assert report.received == (YESTERDAY,)
    assert ("GET", f"/backups/v1/files/{NEW}") not in server.requests


def test_leftover_partial_is_removed(tmp_path: Path) -> None:
    leftover = tmp_path / f".{NEW}.partial"
    leftover.write_bytes(b"half")
    puller(Server({}), tmp_path).pull()
    assert not leftover.exists()


# --- отказ ---


def test_interrupted_download_leaves_nothing_and_is_not_confirmed(tmp_path: Path) -> None:
    server = Server({NEW: b"copy-2"})
    server.broken.add(NEW)
    report = puller(server, tmp_path).pull()
    assert report.rejected == (NEW,) and report.received == ()
    assert list(tmp_path.iterdir()) == []
    assert received_confirmations(server) == 0


def test_wrong_checksum_is_rejected_and_removed(tmp_path: Path) -> None:
    server = Server({NEW: b"copy-2"})
    server.body_for[NEW] = b"copy-X"
    report = puller(server, tmp_path).pull()
    assert report.rejected == (NEW,)
    assert list(tmp_path.iterdir()) == []
    assert received_confirmations(server) == 0


def test_more_bytes_than_announced_are_rejected(tmp_path: Path) -> None:
    server = Server({NEW: b"copy-2"})
    server.body_for[NEW] = b"copy-2" + b"extra"
    assert puller(server, tmp_path).pull().rejected == (NEW,)
    assert list(tmp_path.iterdir()) == []


def test_unauthorized_listing_is_an_error(tmp_path: Path) -> None:
    client = httpx2.Client(base_url="https://prices.example.ru", transport=httpx2.MockTransport(Server({})))
    with pytest.raises(PullError):
        BackupPuller(client, "wrong-" + "t" * 40, tmp_path, 100, lambda: NOW).pull()


ITEM = {"name": NEW, "size_bytes": 6, "sha256": "a" * 64}


@pytest.mark.parametrize(
    "listing",
    [
        {"items": [{**ITEM, "url": "http://192.168.1.1/"}]},
        {"items": [ITEM], "next": "x"},
        {"items": [{**ITEM, "name": "../../etc/passwd"}]},
        {"items": [{**ITEM, "name": "pricewatch-2026-11-28T030000Z.tar.age/../x"}]},
        {"items": [{**ITEM, "size_bytes": "6"}]},
        {"items": [{**ITEM, "size_bytes": True}]},
        {"items": [{**ITEM, "size_bytes": 0}]},
        {"items": [{**ITEM, "sha256": "A" * 64}]},
        {"items": [{key: value for key, value in ITEM.items() if key != "sha256"}]},
        {"items": {"name": NEW}},
        [ITEM],
    ],
    ids=["extra-field", "extra-top-field", "path-traversal", "slash-in-name", "size-as-string", "size-as-bool", "zero-size", "bad-sha", "missing-field", "items-not-list", "not-object"],
)
def test_listing_not_matching_schema_is_rejected(listing: object) -> None:
    with pytest.raises(PullError):
        parse_listing(json.dumps(listing).encode(), 10_000)


def test_listing_bigger_than_limit_is_rejected(tmp_path: Path) -> None:
    server = Server({}, listing=b'{"items": []}' + b" " * LIST_LIMIT)
    with pytest.raises(PullError):
        puller(server, tmp_path).pull()


def test_not_json_listing_is_rejected() -> None:
    with pytest.raises(PullError):
        parse_listing(b"<html>", 10_000)
