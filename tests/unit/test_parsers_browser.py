from pathlib import Path

from pytest import MonkeyPatch

from pricewatch.parsers import browser


class Context:
    def __init__(self) -> None:
        self.closed = False
        self.browser = self

    def is_connected(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


class Driver:
    def __init__(self) -> None:
        self.chromium = self
        self.launches: list[Path] = []
        self.stopped = False

    def start(self) -> "Driver":
        return self

    def stop(self) -> None:
        self.stopped = True

    def launch_persistent_context(self, profile: str, **options: object) -> Context:
        self.launches.append(Path(profile))
        return Context()


def test_alternating_stores_reuse_profiles_and_evict_least_recent(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    driver = Driver()
    monkeypatch.setattr(browser, "sync_playwright", lambda: driver)
    fetcher = browser.BrowserFetcher(tmp_path)
    try:
        first = fetcher.context("a")
        second = fetcher.context("b")
        assert fetcher.context("a") is first
        assert fetcher.context("b") is second
        assert fetcher.context("a") is first
        third = fetcher.context("c")
        assert not first.closed
        assert second.closed
        assert not third.closed
        assert [path.name for path in driver.launches] == ["a", "b", "c"]
    finally:
        fetcher.close()
    assert first.closed and third.closed and driver.stopped


def test_disconnected_profile_is_reopened(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    driver = Driver()
    monkeypatch.setattr(browser, "sync_playwright", lambda: driver)
    fetcher = browser.BrowserFetcher(tmp_path)
    try:
        original = fetcher.context("a")
        original.close()
        assert fetcher.context("a") is not original
        assert len(driver.launches) == 2
    finally:
        fetcher.close()


def test_reset_preserves_other_open_profiles(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    driver = Driver()
    monkeypatch.setattr(browser, "sync_playwright", lambda: driver)
    fetcher = browser.BrowserFetcher(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    try:
        first = fetcher.context("a")
        second = fetcher.context("b")
        fetcher._reset_profile("a")
        assert first.closed and not (tmp_path / "a").exists()
        assert not second.closed and (tmp_path / "b").exists()
        assert fetcher.context("b") is second
    finally:
        fetcher.close()
