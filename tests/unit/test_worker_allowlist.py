"""Слой 1 изоляции домашнего worker: локальный список доменов и проверка адреса задания до любой проверки;
команда python -m pricewatch.worker.allow; суточный лимит заданий.

Резолвер подменён: так видно подмену DNS (домен из списка ведёт в частную сеть) без настоящего DNS."""

from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.worker.allow import main as allow
from pricewatch.worker.allowlist import AllowlistUnusable, EgressGate, allowed, read_domains
from pricewatch.worker.limits import DailyLimitReached, DailyTaskLimit

PUBLIC = [ip_address("93.184.216.34")]


def resolver(table: dict[str, list[str]]):
    def resolve(host: str) -> list:
        if host not in table:
            raise OSError("NXDOMAIN")
        return [ip_address(item) for item in table[host]]
    return resolve


@pytest.fixture
def allowlist(tmp_path: Path) -> Path:
    path = tmp_path / "allowlist.txt"
    path.write_text("# магазины\nozon.ru\nmarket.yandex.ru\n\n", encoding="utf-8")
    return path


def gate(path: Path, table: dict[str, list[str]] | None = None) -> EgressGate:
    return EgressGate(path, resolver(table or {"www.ozon.ru": ["93.184.216.34"], "ozon.ru": ["93.184.216.34"], "market.yandex.ru": ["93.184.216.34"]}))


# --- обычный путь ---


def test_listed_domain_and_subdomain_pass(allowlist: Path) -> None:
    gate(allowlist).check("https://www.ozon.ru/product/123/")
    gate(allowlist).check("https://market.yandex.ru/product--x/1")


def test_list_is_reread_on_every_task(allowlist: Path) -> None:
    check = gate(allowlist, {"citilink.ru": ["93.184.216.34"]})
    with pytest.raises(PriceWatcherError):
        check.check("https://citilink.ru/product/1/")
    assert allow(["--file", str(allowlist), "add", "citilink.ru"]) == 0
    check.check("https://citilink.ru/product/1/")


def test_allow_command_adds_lists_and_removes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "allowlist.txt"
    assert allow(["--file", str(path), "add", "WWW.Avito.ru"]) == 0
    assert read_domains(path) == {"avito.ru"}
    assert allow(["--file", str(path), "list"]) == 0
    assert "avito.ru" in capsys.readouterr().out
    assert allow(["--file", str(path), "remove", "avito.ru"]) == 0
    assert read_domains(path) == frozenset()


# --- граница: похожие имена и пустой список ---


@pytest.mark.parametrize("host", ["notozon.ru", "ozon.ru.evil.example", "yandex.ru", "evil-market.yandex.ru.example"])
def test_lookalike_hosts_are_not_allowed(host: str) -> None:
    assert not allowed(host, {"ozon.ru", "market.yandex.ru"})


def test_empty_list_allows_nothing(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.txt"
    path.write_text("# пусто\n", encoding="utf-8")
    with pytest.raises(PriceWatcherError) as caught:
        gate(path).check("https://www.ozon.ru/product/1/")
    assert caught.value.code is ErrorCode.DOMAIN_NOT_ALLOWED


# --- отказ ---


def test_domain_not_in_list_is_refused(allowlist: Path) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        gate(allowlist, {"evil.example": ["93.184.216.34"]}).check("https://evil.example/p/1")
    assert caught.value.code is ErrorCode.DOMAIN_NOT_ALLOWED


@pytest.mark.parametrize(
    "addresses",
    [["192.168.1.1"], ["10.0.0.5"], ["172.16.0.1"], ["100.64.0.1"], ["127.0.0.1"], ["169.254.169.254"], ["fd00::1"], ["fe80::1"],
     ["93.184.216.34", "192.168.1.1"]],
    ids=["home-lan", "ten", "172-16", "cgnat", "loopback", "metadata", "ipv6-ula", "ipv6-link-local", "one-of-many-private"],
)
def test_listed_domain_resolving_to_private_address_is_refused(allowlist: Path, addresses: list[str]) -> None:
    # Подмена DNS: домен из списка вдруг ведёт в домашнюю сеть.
    with pytest.raises(PriceWatcherError) as caught:
        gate(allowlist, {"www.ozon.ru": addresses}).check("https://www.ozon.ru/product/1/")
    assert caught.value.code is ErrorCode.INVALID_URL


@pytest.mark.parametrize("url", ["https://www.ozon.ru:8443/product/1/", "https://192.168.1.1/", "ftp://ozon.ru/x", "https://user@ozon.ru/x"])
def test_wrong_port_scheme_or_ip_literal_is_refused(allowlist: Path, url: str) -> None:
    with pytest.raises(PriceWatcherError):
        gate(allowlist).check(url)


def test_unresolvable_listed_domain_is_refused(allowlist: Path) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        gate(allowlist, {"other.example": ["93.184.216.34"]}).check("https://ozon.ru/product/1/")
    assert caught.value.code is ErrorCode.INVALID_URL


def test_missing_or_broken_list_stops_worker(tmp_path: Path) -> None:
    with pytest.raises(AllowlistUnusable):
        gate(tmp_path / "missing.txt").check("https://ozon.ru/")
    broken = tmp_path / "broken.txt"
    broken.write_text("ozon.ru\nhttp://bad host/\n", encoding="utf-8")
    with pytest.raises(AllowlistUnusable):
        read_domains(broken)


def test_allow_command_refuses_bad_domain_and_keeps_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.txt"
    path.write_text("ozon.ru\nhttp://bad host/\n", encoding="utf-8")
    assert allow(["--file", str(path), "add", "avito.ru"]) == 2
    assert path.read_text(encoding="utf-8") == "ozon.ru\nhttp://bad host/\n"
    assert allow(["--file", str(tmp_path / "new.txt"), "add", "bad host"]) == 2


# --- суточный лимит заданий ---

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def test_daily_limit_counts_persists_and_stops(tmp_path: Path) -> None:
    counter = tmp_path / "daily.json"
    limit = DailyTaskLimit(2, counter, lambda: NOW)
    limit.ensure_room()
    limit.taken()
    # Перезапуск worker не обнуляет счётчик.
    again = DailyTaskLimit(2, counter, lambda: NOW)
    again.ensure_room()
    again.taken()
    with pytest.raises(DailyLimitReached):
        again.ensure_room()


def test_new_moscow_day_starts_from_zero(tmp_path: Path) -> None:
    counter = tmp_path / "daily.json"
    DailyTaskLimit(1, counter, lambda: NOW).taken()
    # 21:00 UTC — уже следующие сутки по Москве.
    DailyTaskLimit(1, counter, lambda: NOW.replace(hour=21)).ensure_room()
    with pytest.raises(DailyLimitReached):
        DailyTaskLimit(1, counter, lambda: NOW + timedelta(hours=8)).ensure_room()


def test_broken_counter_counts_as_exhausted(tmp_path: Path) -> None:
    counter = tmp_path / "daily.json"
    counter.write_text("{not json", encoding="utf-8")
    with pytest.raises(DailyLimitReached):
        DailyTaskLimit(5, counter, lambda: NOW).ensure_room()
