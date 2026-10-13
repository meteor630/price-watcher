"""tools/import_legacy.py: правила переноса старых данных — цены, проверки, окна ссылок, запуски."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from pricewatch.domain.review import Decision

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_legacy", PROJECT_ROOT / "tools" / "import_legacy.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def check(status: str = "ok", price: str | None = "1000.0", **changes: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "id": 1, "url_id": 1, "run_id": 1, "checked_at": at(2), "status": status, "price": price, "card_price": None,
        "title": "Лампа", "source": "JSON-LD", "decision": None,
    }
    values.update(changes)
    return tool.Check(**values)


def listing(state: str = "active", created_at: datetime = at(2, 10)):  # type: ignore[no-untyped-def]
    return tool.Listing(7, "p1", "shop", state, None, created_at)


def url(url_id: int, closed: bool = False):  # type: ignore[no-untyped-def]
    return tool.ListingUrl(url_id, 7, f"https://shop.example/p/{url_id}", closed)


# --- цены ---


@pytest.mark.parametrize(("raw", "kopecks"), [("64990.0", 6_499_000), ("0.5", 50), ("133021", 13_302_100), (None, None)])
def test_prices_become_exact_kopecks(raw: str | None, kopecks: int | None) -> None:
    assert tool.to_minor(raw) == kopecks


@pytest.mark.parametrize("raw", ["12.345", "0", "-5.0", "abc"])
def test_price_outside_whole_positive_kopecks_stops_import(raw: str) -> None:
    with pytest.raises(tool.LegacyError):
        tool.to_minor(raw)


# --- проверки ---


def test_ok_check_is_confirmed_in_stock_price() -> None:
    mapped = tool.map_check(check(card_price="950.0"))
    assert mapped == tool.MappedCheck("in_stock", 100_000, 95_000, "Лампа", "confirmed", "site_data", None)


def test_out_of_stock_may_have_no_price() -> None:
    mapped = tool.map_check(check("out_of_stock", None, source=None))
    assert (mapped.availability, mapped.price_minor, mapped.source) == ("out_of_stock", None, None)


def test_price_from_page_text_keeps_its_source() -> None:
    assert tool.map_check(check(source="на странице")).source == "page"


def test_open_suspect_waits_for_decision() -> None:
    mapped = tool.map_check(check("suspect"))
    assert (mapped.confirmation, mapped.decision) == ("pending", None)


def test_rejected_suspect_keeps_owner_decision() -> None:
    mapped = tool.map_check(check("suspect", decision="rejected"))
    assert (mapped.confirmation, mapped.decision) == ("pending", Decision.REJECT)


def test_blank_title_is_absent() -> None:
    assert tool.map_check(check(title="  ")).title is None


@pytest.mark.parametrize("status", ["error", "gone"])
def test_checks_without_price_are_not_imported(status: str) -> None:
    assert tool.map_check(check(status, None, source=None)) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "broken"},
        {"source": "rss"},
        {"price": None},
        {"decision": "rejected"},
        {"status": "suspect", "decision": "maybe"},
    ],
)
def test_unexpected_check_stops_import(changes: dict[str, object]) -> None:
    with pytest.raises(tool.LegacyError):
        tool.map_check(check(**changes))


# --- окна ссылок ---


def test_single_address_starts_when_link_was_added() -> None:
    windows = tool.link_windows(listing(), [url(1)], {1: [at(2, 11), at(3)]})
    assert windows == [tool.Window(url(1), at(2, 10), None)]


def test_first_check_before_link_creation_moves_start_back() -> None:
    windows = tool.link_windows(listing(created_at=at(2, 15)), [url(1)], {1: [at(2, 11)]})
    assert windows[0].valid_from == at(2, 11)


def test_replacement_boundary_is_first_check_of_new_address() -> None:
    windows = tool.link_windows(listing(), [url(1, closed=True), url(2)], {1: [at(2, 11), at(3)], 2: [at(4), at(5)]})
    assert windows == [tool.Window(url(1, closed=True), at(2, 10), at(4)), tool.Window(url(2), at(4), None)]


def test_address_replaced_before_any_check_is_skipped() -> None:
    windows = tool.link_windows(listing(), [url(1, closed=True), url(2)], {2: [at(4)]})
    assert windows == [tool.Window(url(2), at(2, 10), None)]


def test_unchecked_open_address_keeps_window() -> None:
    assert tool.link_windows(listing(), [url(1)], {}) == [tool.Window(url(1), at(2, 10), None)]


def test_archived_listing_keeps_its_address_open() -> None:
    assert tool.link_windows(listing("archived"), [url(1)], {1: [at(3)]})[0].valid_to is None


@pytest.mark.parametrize(
    ("state", "urls", "checked"),
    [
        ("disabled", [url(1)], {1: [at(3)]}),
        ("active", [url(1, closed=True), url(2)], {1: [at(3)]}),
        ("active", [url(1, closed=True), url(2)], {1: [at(3), at(6)], 2: [at(4)]}),
        ("active", [url(1), url(2, closed=True)], {1: [at(3)], 2: [at(4)]}),
    ],
    ids=["disabled-link", "replacement-never-checked", "old-address-checked-after-switch", "last-address-closed"],
)
def test_windows_that_cannot_be_rebuilt_stop_import(state: str, urls: list[object], checked: dict[int, list[datetime]]) -> None:
    with pytest.raises(tool.LegacyError):
        tool.link_windows(listing(state), urls, checked)


# --- запуски ---


def test_daily_run_becomes_summarized_scheduled_run_with_own_slot() -> None:
    values = tool.run_values(tool.Run(5, "daily", at(3, 6), at(3, 7), 10, 2), 42, 7)
    assert values == {
        "kind": "scheduled", "slot_key": "legacy-5", "status": "finished", "requested_by": None, "total": 10, "done": 7, "failed": 2,
        "created_at": at(3, 6), "started_at": at(3, 6), "finished_at": at(3, 7), "summarized_at": at(3, 7),
    }


def test_manual_run_is_requested_by_owner_and_total_covers_counts() -> None:
    values = tool.run_values(tool.Run(6, "manual", at(3, 6), at(3, 7), 3, 1), 42, 5)
    assert (values["kind"], values["slot_key"], values["requested_by"], values["total"]) == ("manual", None, 42, 6)


@pytest.mark.parametrize("run", [tool.Run(1, "hourly", at(3), at(4), 0, 0), tool.Run(1, "daily", at(3), None, 0, 0)], ids=["unknown-kind", "unfinished"])
def test_unexpected_run_stops_import(run: object) -> None:
    with pytest.raises(tool.LegacyError):
        tool.run_values(run, 42, 0)
