"""Адаптер Wildberries на сохранённых ответах: цена, наличие, регион, защита, изменившийся формат.

Образцы в tests/fixtures/stores/wildberries сняты 06.10.2026 с настоящего сайта: карточка
товара 1136432918 и ответ сервиса геоданных (без IP и подписи). Остальные случаи — из них же
с изменёнными полями. Браузер подменён: он отдаёт страницу и ответы по адресу запроса.
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserPage, CapturedJson, JsonReply, OpenPage
from pricewatch.parsers.contract import ParsedProduct
from pricewatch.parsers.stores.wildberries import WildberriesParser, article_id
from pricewatch.parsers.urls import NormalizedUrl, normalize_url

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "stores" / "wildberries"
DETAIL = json.loads((FIXTURES / "detail_in_stock.json").read_text(encoding="utf-8"))
GEO = json.loads((FIXTURES / "geo_spb.json").read_text(encoding="utf-8"))
ARTICLE = 1136432918
PAGE_URL = f"https://www.wildberries.ru/catalog/{ARTICLE}/detail.aspx"
SITE_HEADERS = {"deviceid": "site_test", "x-requested-with": "XMLHttpRequest", "x-spa-version": "14.27.2", "accept": "*/*"}
CDN = f"https://basket-43.wbbasket.ru/vol11364/part1136432/{ARTICLE}/info/ru/card.json"


def site_page(*, status: int = 200, html: str = "<html>Wildberries</html>", captured: list[CapturedJson] | None = None) -> BrowserPage:
    if captured is None:
        captured = [
            CapturedJson(f"https://www.wildberries.ru/__internal/u-card/cards/v4/detail?dest=1259570991&nm={ARTICLE}", "{}", dict(SITE_HEADERS)),
            CapturedJson(CDN, "{}", {}),
        ]
    return BrowserPage(normalize_url(PAGE_URL), PAGE_URL, status, html, captured)


class FakeOpen(OpenPage):
    def __init__(self, page: BrowserPage, replies: Callable[[str, dict[str, str] | None], JsonReply], calls: list) -> None:
        super().__init__(page, None)
        self.replies = replies
        self.calls = calls

    def fetch_json(self, requests: list[NormalizedUrl], headers: dict[str, str] | None = None) -> list[JsonReply]:
        result = []
        for request in requests:
            self.calls.append((request.url, headers))
            result.append(self.replies(request.url, headers))
        return result


class FakeBrowser:
    def __init__(self, page: BrowserPage, geo: object = GEO, detail: object = DETAIL, *, detail_status: int = 200) -> None:
        self.page, self.geo, self.detail, self.detail_status = page, geo, detail, detail_status
        self.calls: list[tuple[str, dict[str, str] | None]] = []
        self.opened: list[tuple[str, str]] = []

    def open(self, url: NormalizedUrl, store_key: str, ready, timeout_s: float = 25.0) -> OpenPage:  # type: ignore[no-untyped-def]
        self.opened.append((url.url, store_key))
        assert ready(self.page) == any("/cards/v4/detail" in c.url for c in self.page.json)
        return FakeOpen(self.page, self._reply, self.calls)

    def _reply(self, url: str, headers: dict[str, str] | None) -> JsonReply:
        if "user-geo-data" in url:
            return JsonReply(200, self.geo if isinstance(self.geo, str) else json.dumps(self.geo, ensure_ascii=False))
        body = self.detail if isinstance(self.detail, str) else json.dumps(self.detail, ensure_ascii=False)
        return JsonReply(self.detail_status, body if self.detail_status == 200 else "")


def parse(browser: FakeBrowser, url: str = PAGE_URL, region: str = "spb") -> ParsedProduct:
    return WildberriesParser(browser).parse(normalize_url(url), region)


def error(browser: FakeBrowser, **kwargs: str) -> PriceWatcherError:
    with pytest.raises(PriceWatcherError) as caught:
        parse(browser, **kwargs)
    return caught.value


def detail_with(**changes: object) -> dict:
    data = copy.deepcopy(DETAIL)
    data["products"][0].update(changes)
    return data


# --- обычный случай ---


def test_price_title_image_and_region_from_saved_sample() -> None:
    assert parse(FakeBrowser(site_page())) == ParsedProduct(
        "Лампа настольная светодиодная",
        61_800,
        None,
        Availability.IN_STOCK,
        f"https://basket-43.wbbasket.ru/vol11364/part1136432/{ARTICLE}/images/big/1.webp",
        "spb",
    )


def test_card_is_requested_for_region_dest_with_site_headers() -> None:
    browser = FakeBrowser(site_page())

    parse(browser, url=f"https://wildberries.ru/catalog/{ARTICLE}/detail.aspx?targetUrl=GP&size=1")

    assert browser.opened == [(PAGE_URL, "wildberries")]  # адрес приведён к карточке, профиль магазина
    (geo_url, geo_headers), (detail_url, detail_headers) = browser.calls
    assert parse_qs(urlsplit(geo_url).query)["address"] == ["Санкт-Петербург"]
    assert geo_headers is None
    query = parse_qs(urlsplit(detail_url).query)
    assert (query["dest"], query["nm"]) == (["-1185367"], [str(ARTICLE)])
    assert detail_headers == {"deviceid": "site_test", "x-requested-with": "XMLHttpRequest", "x-spa-version": "14.27.2"}


def test_cheapest_size_wins() -> None:
    sizes = [{"price": {"product": 70_000}}, {"price": {"product": 65_500}}, {"price": {}}]

    assert parse(FakeBrowser(site_page(), detail=detail_with(sizes=sizes))).price_minor == 65_500


def test_out_of_stock() -> None:
    parsed = parse(FakeBrowser(site_page(), detail=detail_with(totalQuantity=0, sizes=[{"stocks": []}])))

    assert (parsed.availability, parsed.price_minor) == (Availability.OUT_OF_STOCK, None)


def test_image_absent_when_page_did_not_load_card_files() -> None:
    page = site_page(captured=[site_page().json[0]])

    assert parse(FakeBrowser(page)).image_url is None


@pytest.mark.parametrize(
    ("raw", "article"),
    [
        (f"https://www.wildberries.ru/catalog/{ARTICLE}/detail.aspx", ARTICLE),
        (f"https://m.wildberries.ru/catalog/{ARTICLE}/detail.aspx?size=123", ARTICLE),
        ("https://www.wildberries.ru/catalog/1/detail.aspx", 1),
    ],
)
def test_article_from_address(raw: str, article: int) -> None:
    assert article_id(normalize_url(raw)) == article


@pytest.mark.parametrize(
    "raw",
    ["https://www.wildberries.ru/", "https://www.wildberries.ru/catalog/elektronika", "https://www.wildberries.ru/brands/x/catalog/1/"],
)
def test_address_without_article_is_invalid(raw: str) -> None:
    assert error(FakeBrowser(site_page()), url=raw).code is ErrorCode.INVALID_URL


# --- отказы ---


def test_article_missing_from_answer_means_no_price() -> None:
    assert error(FakeBrowser(site_page(), detail={"products": []})).code is ErrorCode.PRICE_NOT_FOUND


def test_other_city_is_region_mismatch() -> None:
    caught = error(FakeBrowser(site_page(), geo={**GEO, "address": "Москва", "xinfo": "dest=-1257786"}))

    assert caught.code is ErrorCode.REGION_MISMATCH
    assert caught.details["city"] == "Москва"


def test_unknown_region_code_is_region_mismatch() -> None:
    assert error(FakeBrowser(site_page()), region="nsk").code is ErrorCode.REGION_MISMATCH


def test_protection_page_without_site_request_is_blocked() -> None:
    page = site_page(status=498, html="<script src='/__wbaas/challenges/antibot'></script>", captured=[])

    assert error(FakeBrowser(page)).code is ErrorCode.BLOCKED


def test_page_without_site_request_and_without_protection_is_layout_change() -> None:
    assert error(FakeBrowser(site_page(captured=[]))).code is ErrorCode.LAYOUT_CHANGED


def test_site_request_without_deviceid_is_layout_change() -> None:
    page = site_page(captured=[CapturedJson(f"https://www.wildberries.ru/__internal/u-card/cards/v4/detail?nm={ARTICLE}", "{}", {})])

    assert error(FakeBrowser(page)).code is ErrorCode.LAYOUT_CHANGED


@pytest.mark.parametrize(("status", "code"), [(403, ErrorCode.BLOCKED), (498, ErrorCode.BLOCKED), (502, ErrorCode.TIMEOUT), (404, ErrorCode.LAYOUT_CHANGED)])
def test_card_request_refused(status: int, code: ErrorCode) -> None:
    assert error(FakeBrowser(site_page(), detail_status=status)).code is code


@pytest.mark.parametrize(
    ("geo", "detail"),
    [
        ("<html>не JSON</html>", DETAIL),
        ({"address": "Санкт-Петербург"}, DETAIL),  # нет xinfo
        ({**GEO, "xinfo": "appType=1&curr=rub"}, DETAIL),  # нет dest
        (GEO, "{broken"),
        (GEO, {"data": []}),  # другой формат ответа
        (GEO, detail_with(totalQuantity="много")),
        (GEO, detail_with(totalQuantity=5, sizes=[{"price": {}}])),  # в наличии, но без цены
    ],
)
def test_changed_format_is_layout_change(geo: object, detail: object) -> None:
    assert error(FakeBrowser(site_page(), geo=geo, detail=detail)).code is ErrorCode.LAYOUT_CHANGED


def test_long_title_is_cut_to_limit() -> None:
    assert len(parse(FakeBrowser(site_page(), detail=detail_with(name="Лампа " * 200))).title or "") == 500
