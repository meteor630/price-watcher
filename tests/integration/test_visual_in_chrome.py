"""Цена «глазами» в настоящем Chrome: страница с подвохами, нужна именно цена товара.

Интернет не нужен: страница задаётся прямо в браузер. Нужен установленный Google Chrome и экран.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from pricewatch.domain.pricing import Availability
from pricewatch.parsers.browser import BrowserFetcher
from pricewatch.parsers.visual import VISUAL_JS, availability, pick_price

PAGE = """
<html><body style="font-family: sans-serif">
  <header><span style="font-size: 12px">Бесплатная доставка от 3 000 ₽</span></header>
  <h1>Лампа настольная «Ёжик»</h1>
  <div><s style="font-size: 18px">15 990 ₽</s></div>
  <div style="font-size: 34px; font-weight: 700">12 990 ₽</div>
  <div style="font-size: 20px">от 1 083 ₽/мес в рассрочку</div>
  <div>Платите <b style="font-size: 44px; font-weight: 900">2 165 ₽</b> в месяц</div>
  <div style="font-size: 14px">Бонусы: 650 ₽ баллами</div>
  <button>В корзину</button>
  <div style="height: 2000px"></div>
  <section><h3>Похожие товары</h3><div style="font-size: 40px; font-weight: 800">990 ₽</div></section>
</body></html>
"""


@pytest.fixture
def browser(tmp_path: Path) -> Iterator[BrowserFetcher]:
    fetcher = BrowserFetcher(tmp_path / "profiles", resolver=lambda host: [])
    yield fetcher
    fetcher.close()


def run(browser: BrowserFetcher, html: str) -> object:
    tab = browser.context("visual-test").new_page()
    tab.set_content(html)
    result = tab.evaluate(VISUAL_JS)
    tab.close()
    return result


def test_product_price_among_traps(browser: BrowserFetcher) -> None:
    probe = run(browser, PAGE)

    chosen = pick_price(probe)
    assert chosen is not None and chosen.price_minor == 1_299_000
    assert availability(probe) is Availability.IN_STOCK
    assert probe["h1"] == "Лампа настольная «Ёжик»"  # type: ignore[index]


def test_out_of_stock_button(browser: BrowserFetcher) -> None:
    probe = run(browser, "<html><body><h1>Лампа</h1><div style='font-size:30px'>5 490 ₽</div><button>Сообщить о поступлении</button></body></html>")

    assert availability(probe) is Availability.OUT_OF_STOCK
    assert pick_price(probe).price_minor == 549_000  # type: ignore[union-attr]


def test_hidden_price_is_ignored(browser: BrowserFetcher) -> None:
    probe = run(browser, "<html><body><h1>Лампа</h1><div style='visibility:hidden;font-size:40px'>1 ₽</div><div>2 490 ₽</div></body></html>")

    assert pick_price(probe).price_minor == 249_000  # type: ignore[union-attr]
