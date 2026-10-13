"""Страницы сайта в настоящем Chrome (SITE_DESIGN_SPEC.md, раздел 12): на ширинах 320 и 1440 в светлой и тёмной теме
у каждой кнопки, ссылки, иконки и поля есть подпись, контраст текста по WCAG AA, текст не вылезает из блоков,
страница не шире экрана, на телефоне цели нажатия не меньше 24 px и шапка в одну строку, интерактивное — только кнопки и ссылки,
шапка не закрывает заголовок, ошибок в консоли нет; диалоги с клавиатуры принимают фокус, закрываются Escape и
возвращают фокус; при «уменьшить движение» анимаций нет.

Нужен установленный Google Chrome (окно не нужно). Данные — все состояния раздела 11 ТЗ (ui_site.seed).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from patchright.sync_api import Browser, Page, sync_playwright
from sqlalchemy import Engine
from ui_site import MOTION_JS, PAGE_CHECKS_JS, PASSWORD, Seeded, live_site, seed

from pricewatch.persistence.database import create_db_engine

WIDTHS = (320, 1440)
THEMES = ("light", "dark")
ADMIN_PAGES = [
    "home", "category-states", "category-long", "category-many", "category-empty", "group", "group-empty", "item-long", "item-huge",
    "item-stale", "item-pending", "item-no-links", "item-awaiting", "item-purchased", "item-archived", "purchases", "archive",
    "settings-items", "settings-categories", "settings-profile", "settings-stores", "settings-check", "settings-users",
]
USER_PAGES = ["home", "settings-items", "settings-profile"]
DIALOG_PAGES = ["settings-categories", "settings-items", "settings-stores", "settings-users", "item-long", "item-purchased"]


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture(scope="module")
def site(engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Seeded]]:
    seeded = seed(engine)
    with live_site(engine, tmp_path_factory.mktemp("media")) as origin:
        yield origin, seeded


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        chrome = playwright.chromium.launch(channel="chrome", headless=True)
        yield chrome
        chrome.close()


@pytest.fixture(scope="module")
def sessions(browser: Browser, site: tuple[str, Seeded]) -> dict[str, dict]:
    origin, seeded = site
    states = {}
    for who, login in (("admin", seeded.admin), ("user", seeded.user)):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(origin + "/web/login")
        page.fill("#login", login)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(origin + "/web/")
        states[who] = context.storage_state()
        context.close()
    return states


def open_page(browser: Browser, origin: str, path: str, state: dict | None, width: int, theme: str, status: int = 200, **options: object) -> tuple[Page, list[str]]:
    context = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme, storage_state=state, is_mobile=width < 768,  # type: ignore[arg-type]
                                  has_touch=width < 768, **options)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(f"ошибка скрипта: {error}"))
    page.on("console", lambda message: errors.append(f"консоль: {message.text}") if message.type == "error" and not message.text.startswith("Failed to load resource") else None)
    page.on("response", lambda response: errors.append(f"HTTP {response.status} {response.url}") if response.status >= 400 and response.request.resource_type != "document" else None)
    response = page.goto(origin + path)
    assert response is not None and response.status == status, f"{path}: HTTP {response.status if response else '—'}"
    page.wait_for_load_state("networkidle")
    return page, errors


def violations(browser: Browser, origin: str, path: str, state: dict | None, status: int = 200) -> list[str]:
    found = []
    for width in WIDTHS:
        for theme in THEMES:
            page, errors = open_page(browser, origin, path, state, width, theme, status)
            found += [f"{width} {theme}: {problem}" for problem in page.evaluate(PAGE_CHECKS_JS) + errors]
            page.context.close()
    return found


@pytest.mark.parametrize("name", ADMIN_PAGES)
def test_admin_pages_meet_accessibility_rules(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], name: str) -> None:
    origin, seeded = site

    assert violations(browser, origin, seeded.pages[name], sessions["admin"]) == []


@pytest.mark.parametrize("name", USER_PAGES)
def test_user_pages_meet_accessibility_rules(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], name: str) -> None:
    origin, seeded = site

    assert violations(browser, origin, seeded.pages[name], sessions["user"]) == []


@pytest.mark.parametrize(
    ("path", "who", "status"),
    [("/web/login", None, 200), ("/web/register?code=" + "A" * 22, None, 400), ("/web/register", None, 400), ("/web/items/999999999", "admin", 404)],
)
def test_entry_and_error_pages_meet_accessibility_rules(
    browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], path: str, who: str | None, status: int
) -> None:
    origin, _ = site

    assert violations(browser, origin, path, sessions[who] if who else None, status) == []


@pytest.mark.parametrize("name", DIALOG_PAGES)
def test_dialogs_take_and_return_keyboard_focus(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], name: str) -> None:
    origin, seeded = site
    page, _ = open_page(browser, origin, seeded.pages[name], sessions["admin"], 1440, "dark")
    labels = sorted({text.strip() for text in page.locator('button[hx-target="#dialog"]:visible').all_inner_texts()})
    problems = []

    for label in labels:
        page.goto(origin + seeded.pages[name])
        page.wait_for_load_state("networkidle")
        trigger = page.locator('button[hx-target="#dialog"]:visible', has_text=label).first
        trigger.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector("dialog[open]")
        if not page.evaluate("() => !!document.activeElement.closest('dialog[open]')"):
            problems.append(f"«{label}»: фокус не в диалоге")
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('dialog[open]')")
        if not trigger.evaluate("element => element === document.activeElement"):
            problems.append(f"«{label}»: фокус не вернулся на кнопку")
    page.context.close()

    assert labels and problems == []


def test_reduced_motion_removes_animation_everywhere(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict]) -> None:
    origin, seeded = site
    moving = {}

    for name in ADMIN_PAGES:
        page, _ = open_page(browser, origin, seeded.pages[name], sessions["admin"], 1440, "dark", reduced_motion="reduce")
        if leftovers := page.evaluate(MOTION_JS):
            moving[name] = leftovers
        page.context.close()

    assert moving == {}


BROKEN = """
<h1 style="margin-top: 0">Заголовок</h1>
<div style="position: fixed; top: 0; left: 0; right: 0; height: 80px; background: #000">шапка поверх</div>
<button><svg width="16" height="16"><circle cx="8" cy="8" r="4"/></svg></button>
<a href="/x"><svg aria-hidden="true" width="16" height="16"><circle cx="8" cy="8" r="4"/></svg></a>
<input name="price">
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="10" height="10">
<div style="cursor: pointer">кликабельный div</div>
<div hx-get="/web/x">htmx на div</div>
<p style="width: 40px; white-space: nowrap">очень-длинное-слово-без-пробелов</p>
<span style="color: #999; background: #fff">бледный текст</span>
<button style="width: 20px; height: 20px; padding: 0" aria-label="мелкая">x</button>
<header class="top" style="height: 120px">шапка в две строки</header>
<div style="width: 900px; height: 4px"></div>
<p style="width: 60px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis">длинное название без подсказки</p>
<p style="display: flex; flex-wrap: wrap; width: 120px"><span class="store-dot" style="width: 8px; height: 8px; background: red"></span>Интернет-магазин бытовой техники</p>
"""


def test_checks_find_every_kind_of_violation(browser: Browser) -> None:
    page = browser.new_page(viewport={"width": 375, "height": 600})
    page.set_content(BROKEN)

    found = page.evaluate(PAGE_CHECKS_JS)
    page.close()

    for kind in ("без подписи: <button>", "без подписи: <a", "svg без подписи", "поле без подписи", "картинка без alt", "курсор-рука", "htmx не на кнопке",
                 "текст вылезает", "заголовок закрыт", "низкий контраст", "мелкая цель", "шапка выше 64 px", "страница шире экрана", "точка магазина оторвана", "обрезан многоточием"):
        assert any(problem.startswith(kind) for problem in found), (kind, found)


CARDS_JS = """
() => [...document.querySelectorAll('.cats .cat')].map(card => {
  const name = card.querySelector('.cat__name'); const chart = card.querySelector('.cat__chart, .cat__empty');
  const line = parseFloat(getComputedStyle(name).lineHeight);
  return {top: Math.round(card.getBoundingClientRect().top), name: name.innerText, lines: Math.round(name.scrollHeight / line),
          visibleLines: Math.round(name.getBoundingClientRect().height / line), chartBottom: chart ? Math.round(card.getBoundingClientRect().bottom - chart.getBoundingClientRect().bottom) : null};
})
"""


@pytest.mark.parametrize("width", [320, 375, 430])
def test_category_cards_are_even_on_phone(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], width: int) -> None:
    origin, seeded = site
    page, _ = open_page(browser, origin, seeded.pages["home"], sessions["admin"], width, "dark")

    cards = page.evaluate(CARDS_JS)
    problems = page.evaluate(PAGE_CHECKS_JS)
    page.context.close()

    assert problems == []
    assert len(cards) >= 6
    assert all(card["visibleLines"] <= 2 for card in cards), [card for card in cards if card["visibleLines"] > 2]
    rows: dict[int, list[dict]] = {}
    for card in cards:
        rows.setdefault(card["top"], []).append(card)
    for row in rows.values():
        assert len({card["chartBottom"] for card in row if card["chartBottom"] is not None}) <= 1, row  # графики ряда — на одной линии


ROWS_JS = """
() => [...document.querySelectorAll('.cats .cat')].filter(card => card.querySelector('.cat__total')).map(card => {
  const top = card.getBoundingClientRect().top;
  const total = card.querySelector('.cat__total'); const delta = card.querySelector('.cat__delta');
  const text = total.firstChild; const size = parseFloat(getComputedStyle(total).fontSize); const gaps = [];
  for (let i = 0; i < text.data.length; i++) {
    if (text.data[i] === '\u202f' || text.data[i] === '\u00a0') {
      const range = document.createRange(); range.setStart(text, i); range.setEnd(text, i + 1);
      gaps.push(range.getBoundingClientRect().width / size);
    }
  }
  const box = el => el.getBoundingClientRect();
  return {name: card.querySelector('.cat__name').innerText, top: Math.round(top), totalTop: Math.round(box(total).top - top),
          deltaTop: Math.round(box(delta).top - top), deltaHeight: Math.round(box(delta).height), gaps: gaps};
})
"""


@pytest.mark.parametrize("width", [320, 375, 430])
def test_category_card_rows_line_up_on_phone(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], width: int) -> None:
    # Снимок владельца 13.10.2026: в соседних карточках сумма и изменение на разной высоте («наблюдаю» есть не у всех),
    # длинное изменение уводит процент на вторую строку, пробелы между разрядами суммы не видны.
    origin, seeded = site
    page, _ = open_page(browser, origin, seeded.pages["home"], sessions["admin"], width, "dark")

    cards = page.evaluate(ROWS_JS)
    page.context.close()

    rows: dict[int, list[dict]] = {}
    for card in cards:
        rows.setdefault(card["top"], []).append(card)
    if width >= 375:
        assert any(len(row) == 2 for row in rows.values())
    for row in rows.values():
        for field in ("totalTop", "deltaTop", "deltaHeight"):
            assert len({card[field] for card in row}) == 1, (field, row)
    # Пробел между разрядами — не меньше пятой части кегля: его видно.
    assert all(gap >= 0.2 for card in cards for gap in card["gaps"]), [(card["name"], card["gaps"]) for card in cards]


def test_single_review_card_uses_full_width_on_desktop(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict]) -> None:
    origin, seeded = site
    page, _ = open_page(browser, origin, seeded.pages["home"], sessions["admin"], 1440, "dark")

    widths = page.evaluate("() => [document.querySelector('.review-list').getBoundingClientRect().width, ...[...document.querySelectorAll('.review-list > li')].map(li => li.getBoundingClientRect().width)]")
    page.context.close()

    assert len(widths) == 2 and widths[1] == pytest.approx(widths[0], abs=1)


DANGER_JS = """
() => [...document.querySelectorAll('.edit-row__actions')].flatMap(actions => {
  const buttons = [...actions.querySelectorAll('button, a.btn')].filter(b => b.getBoundingClientRect().width > 0);
  if (buttons.length < 2) return [];  // единственное действие строки ставить рядом не с чем
  return buttons.filter(b => b.classList.contains('btn--danger')).filter(danger => {
    const d = danger.getBoundingClientRect();
    return !buttons.some(other => {
      if (other === danger) return false;
      const o = other.getBoundingClientRect();
      return Math.abs(o.top - d.top) < 2 || (Math.abs(o.left - d.left) < 2 && Math.abs(o.width - d.width) < 2);
    });
  }).map(danger => danger.closest('.edit-row').innerText.trim().split('\\n')[0]);
})
"""


@pytest.mark.parametrize("name", ["settings-categories", "settings-items", "settings-stores", "settings-users", "settings-profile"])
@pytest.mark.parametrize("width", [320, 375])
def test_delete_stays_with_other_actions_on_phone(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], name: str, width: int) -> None:
    """«Удалить» — рядом с остальными действиями: в их строке или в их колонке той же ширины, а не одна в новой строке (NOTES.md)."""
    origin, seeded = site
    page, _ = open_page(browser, origin, seeded.pages[name], sessions["admin"], width, "dark")

    lonely = page.evaluate(DANGER_JS)
    page.context.close()

    assert lonely == []


FOCUS_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const cs = getComputedStyle(el);
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
  return {name: (el.getAttribute('aria-label') || el.innerText || el.name || el.tagName).trim().slice(0, 40), ring};
}
"""


@pytest.mark.parametrize("name", ["home", "item-long", "settings-items", "settings-categories", "settings-check", "settings-profile", "settings-users"])
def test_keyboard_focus_is_always_visible(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], name: str) -> None:
    origin, seeded = site
    page, _ = open_page(browser, origin, seeded.pages[name], sessions["admin"], 1440, "light")
    invisible = []

    for _ in range(60):
        page.keyboard.press("Tab")
        focus = page.evaluate(FOCUS_JS)
        if focus is not None and not focus["ring"]:
            invisible.append(focus["name"])
    page.context.close()

    assert invisible == []


ORDER_JS = """
() => [...document.querySelectorAll('.edit-row__actions')].flatMap(actions => {
  const arrows = [...actions.querySelectorAll('.icon-btn')].filter(b => b.getBoundingClientRect().width > 0);
  const problems = [];
  for (let i = 1; i < arrows.length; i++) {
    const a = arrows[i - 1].getBoundingClientRect(), b = arrows[i].getBoundingClientRect();
    if (Math.abs(a.top - b.top) > 1 || b.left - a.right > 16) problems.push(actions.closest('.edit-row').innerText.trim().split('\\n')[0]);
  }
  const texts = [...actions.querySelectorAll(':scope > .btn')].filter(b => b.getBoundingClientRect().width > 0);
  if (new Set(texts.map(b => Math.round(b.getBoundingClientRect().width))).size > 1) problems.push('кнопки разной ширины: ' + actions.closest('.edit-row').innerText.trim().split('\\n')[0]);
  return problems;
})
"""


@pytest.mark.parametrize("name", ["settings-categories", "settings-items", "settings-stores", "settings-users", "settings-profile"])
@pytest.mark.parametrize("width", [320, 375, 430])
def test_row_actions_are_aligned_on_phone(browser: Browser, site: tuple[str, Seeded], sessions: dict[str, dict], name: str, width: int) -> None:
    """Стрелки порядка — рядом друг с другом, кнопки действий — одной ширины по сетке."""
    origin, seeded = site
    page, _ = open_page(browser, origin, seeded.pages[name], sessions["admin"], width, "dark")

    problems = page.evaluate(ORDER_JS)
    page.context.close()

    assert problems == []
