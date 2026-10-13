"""backup.archive: копия — tar с дампом и картинками, зашифрованный age; пароль базы — только в окружении pg_dump.

pg_dump и age подменены программами на Python: «age» дописывает метку и передаёт tar как есть, чтобы его можно было прочитать.
Настоящие pg_dump и age проверяются в CI в образе backup (копия → расшифровка → pg_restore).
"""

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

from pricewatch.application.ports import BackupError
from pricewatch.backup.archive import (
    DATABASE_DUMP_FAILED,
    ENCRYPTION_FAILED,
    MEDIA_UNAVAILABLE,
    STORAGE_FAILED,
    UPLOAD_FAILED,
    AgeArchive,
    DatabaseAddress,
    Programs,
    RemoteFolder,
)

NAME = "pricewatch-2026-10-14T013000Z.tar.age"
PASSWORD = "db-test-password-not-real"
RECIPIENT = "age1" + "q" * 58
FAKE_HEADER = b"FAKE-AGE\n"

FAKE_PG_DUMP = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
Path(os.environ["CALLS"]).write_text(json.dumps({"args": args, "password": os.environ.get("PGPASSWORD")}))
if os.environ.get("FAIL_DUMP"):
    sys.stderr.write("pg_dump: connection refused")
    sys.exit(1)
target = next(arg for arg in args if arg.startswith("--file=")).removeprefix("--file=")
Path(target).write_bytes(b"PGDMP-test-dump")
"""

FAKE_RSYNC = """
import json, os, re, stat, sys
from pathlib import Path
args = sys.argv[1:]
ssh = args[args.index("-e") + 1]
key = Path(re.search(r"-i (\\S+)", ssh).group(1))
Path(os.environ["RSYNC_CALLS"]).write_text(json.dumps({
    "args": args, "ssh": ssh, "key": key.read_text(), "key_private": stat.S_IMODE(key.stat().st_mode) & 0o077 == 0 or os.name == "nt",
}))
if os.environ.get("FAIL_RSYNC"):
    sys.stderr.write("ssh: connect to host tunnel.example.ru port 22: Connection refused")
    sys.exit(255)
"""

FAKE_AGE = """
import os, sys
if sys.argv[1:4] != ["--encrypt", "--recipient", os.environ["RECIPIENT"]]:
    sys.exit(3)
data = sys.stdin.buffer.read()
if os.environ.get("FAIL_AGE"):
    sys.stderr.write("age: bad recipient")
    sys.exit(1)
sys.stdout.buffer.write(b"FAKE-AGE\\n" + data)
"""


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    calls = tmp_path / "calls.json"
    monkeypatch.setenv("CALLS", str(calls))
    monkeypatch.setenv("RSYNC_CALLS", str(tmp_path / "rsync.json"))
    monkeypatch.setenv("RECIPIENT", RECIPIENT)
    return calls


def archive(tmp_path: Path, *, media: bool = True) -> AgeArchive:
    programs_dir = tmp_path / "programs"
    programs_dir.mkdir(exist_ok=True)
    (programs_dir / "pg_dump.py").write_text(FAKE_PG_DUMP, encoding="utf-8")
    (programs_dir / "age.py").write_text(FAKE_AGE, encoding="utf-8")
    (programs_dir / "rsync.py").write_text(FAKE_RSYNC, encoding="utf-8")
    ssh_dir = tmp_path / "ssh"
    ssh_dir.mkdir(exist_ok=True)
    (ssh_dir / "id_ed25519").write_text("test-only-key-material\n", encoding="utf-8")
    (ssh_dir / "known_hosts").write_text("tunnel.example.ru ssh-ed25519 AAAAtest\n", encoding="utf-8")
    folder = tmp_path / "backups"
    folder.mkdir(exist_ok=True)
    media_dir = tmp_path / "media"
    if media:
        (media_dir / "ab").mkdir(parents=True, exist_ok=True)
        (media_dir / "ab" / "photo.jpg").write_bytes(b"jpeg-bytes")
    return AgeArchive(
        folder, media_dir, RECIPIENT, DatabaseAddress("db", 5432, "pricewatch", "pricewatch", PASSWORD),
        RemoteFolder("pwbackup@tunnel.example.ru", 2222, ssh_dir / "id_ed25519", ssh_dir / "known_hosts"),
        programs=Programs(
            (sys.executable, str(programs_dir / "pg_dump.py")), (sys.executable, str(programs_dir / "age.py")),
            (sys.executable, str(programs_dir / "rsync.py")),
        ),
        work_root=tmp_path,
    )


def tar_members(path: Path) -> dict[str, bytes]:
    raw = path.read_bytes()
    assert raw.startswith(FAKE_HEADER)
    with tarfile.open(fileobj=io.BytesIO(raw.removeprefix(FAKE_HEADER))) as tar:
        return {member.name: tar.extractfile(member).read() for member in tar.getmembers() if member.isfile()}  # type: ignore[union-attr]


# --- обычный путь ---


def test_backup_contains_dump_and_media_encrypted_for_recipient(tmp_path: Path, env: Path) -> None:
    folder = archive(tmp_path)
    size = folder.create(NAME)
    target = tmp_path / "backups" / NAME
    assert size == target.stat().st_size
    assert tar_members(target) == {"database.dump": b"PGDMP-test-dump", "media/ab/photo.jpg": b"jpeg-bytes"}


def test_password_goes_only_through_environment(tmp_path: Path, env: Path) -> None:
    archive(tmp_path).create(NAME)
    call = json.loads(env.read_text())
    assert call["password"] == PASSWORD
    assert not any(PASSWORD in arg for arg in call["args"])
    assert {"--format=custom", "--no-password", "--host=db", "--port=5432", "--username=pricewatch", "--dbname=pricewatch"} <= set(call["args"])


def test_unencrypted_dump_does_not_stay_on_disk(tmp_path: Path, env: Path) -> None:
    archive(tmp_path).create(NAME)
    assert not list(tmp_path.rglob("database.dump"))
    assert sorted(path.name for path in (tmp_path / "backups").iterdir()) == [NAME]


def test_names_and_remove(tmp_path: Path, env: Path) -> None:
    folder = archive(tmp_path)
    folder.create(NAME)
    (tmp_path / "backups" / "notes.txt").write_text("x")
    (tmp_path / "backups" / "sub").mkdir()
    assert sorted(folder.names()) == sorted([NAME, "notes.txt"])
    folder.remove(NAME)
    assert folder.names() == ["notes.txt"]


# --- граница: остатки оборванной попытки ---


def test_leftover_partial_files_are_removed_before_new_backup(tmp_path: Path, env: Path) -> None:
    folder = archive(tmp_path)
    leftover = tmp_path / "backups" / ".pricewatch-2026-10-13T013000Z.tar.age.partial"
    leftover.write_bytes(b"half")
    folder.create(NAME)
    assert not leftover.exists()


# --- отказ ---


def test_failed_dump_leaves_no_file(tmp_path: Path, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAIL_DUMP", "1")
    with pytest.raises(BackupError) as caught:
        archive(tmp_path).create(NAME)
    assert caught.value.code == DATABASE_DUMP_FAILED
    assert list((tmp_path / "backups").iterdir()) == []


def test_failed_encryption_leaves_no_file(tmp_path: Path, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAIL_AGE", "1")
    with pytest.raises(BackupError) as caught:
        archive(tmp_path).create(NAME)
    assert caught.value.code == ENCRYPTION_FAILED
    assert list((tmp_path / "backups").iterdir()) == []


def test_missing_media_folder_is_a_failure(tmp_path: Path, env: Path) -> None:
    with pytest.raises(BackupError) as caught:
        archive(tmp_path, media=False).create(NAME)
    assert caught.value.code == MEDIA_UNAVAILABLE


def test_missing_backup_folder_is_a_storage_failure(tmp_path: Path, env: Path) -> None:
    folder = archive(tmp_path)
    (tmp_path / "backups").rmdir()
    with pytest.raises(BackupError) as caught:
        folder.create(NAME)
    assert caught.value.code == STORAGE_FAILED
    with pytest.raises(BackupError):
        folder.names()


def test_removing_missing_copy_is_a_storage_failure(tmp_path: Path, env: Path) -> None:
    with pytest.raises(BackupError) as caught:
        archive(tmp_path).remove(NAME)
    assert caught.value.code == STORAGE_FAILED


# --- отправка на арендный сервер ---

OLDER = "pricewatch-2026-10-13T013000Z.tar.age"


def rsync_call(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "rsync.json").read_text())


def test_upload_sends_only_backups_without_overwriting(tmp_path: Path, env: Path) -> None:
    folder = archive(tmp_path)
    folder.create(NAME)
    (tmp_path / "backups" / OLDER).write_bytes(b"older")
    (tmp_path / "backups" / "notes.txt").write_text("x")
    folder.upload()
    call = rsync_call(tmp_path)
    sent = [Path(arg).name for arg in call["args"] if arg.endswith(".tar.age")]
    assert sent == [OLDER, NAME]
    assert call["args"][-1] == "pwbackup@tunnel.example.ru:"
    assert {"--ignore-existing", "--chmod=F600"} <= set(call["args"])


def test_upload_checks_server_key_strictly_with_private_key_copy(tmp_path: Path, env: Path) -> None:
    folder = archive(tmp_path)
    folder.create(NAME)
    folder.upload()
    call = rsync_call(tmp_path)
    assert "StrictHostKeyChecking=yes" in call["ssh"] and "BatchMode=yes" in call["ssh"] and "-p 2222" in call["ssh"]
    assert f"UserKnownHostsFile={tmp_path / 'ssh' / 'known_hosts'}" in call["ssh"]
    assert call["key"] == "test-only-key-material\n" and call["key_private"]
    # Копия ключа живёт только на время отправки.
    assert not list(tmp_path.rglob("ssh_key"))


def test_empty_folder_sends_nothing(tmp_path: Path, env: Path) -> None:
    archive(tmp_path).upload()
    assert not (tmp_path / "rsync.json").exists()


def test_unreachable_server_is_upload_failure(tmp_path: Path, env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = archive(tmp_path)
    folder.create(NAME)
    monkeypatch.setenv("FAIL_RSYNC", "1")
    with pytest.raises(BackupError) as caught:
        folder.upload()
    assert caught.value.code == UPLOAD_FAILED
    assert (tmp_path / "backups" / NAME).exists()


def test_missing_ssh_key_is_upload_failure(tmp_path: Path, env: Path) -> None:
    folder = archive(tmp_path)
    folder.create(NAME)
    (tmp_path / "ssh" / "id_ed25519").unlink()
    with pytest.raises(BackupError) as caught:
        folder.upload()
    assert caught.value.code == UPLOAD_FAILED
