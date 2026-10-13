"""Сайт в настоящем Chrome для проверок интерфейса (этап 9): сервер в потоке на свободном порту, данные со
состояниями из SITE_DESIGN_SPEC.md (раздел 11), проверки раздела 12 на JavaScript в странице.

Нужен установленный Google Chrome; окно не нужно (headless). Сервер — та же сборка приложения, что в работе.
"""

import socket
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from sqlalchemy import Connection, Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.passwords import PasswordHasher
from pricewatch.config.settings import Settings
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "ui-site-key-" + "k" * 32
PASSWORD = "интерфейс без ошибок 2026"
HASHER = PasswordHasher(time_cost=1, memory_cost=8 * 1024)
DAY = timedelta(days=1)
LONG_ITEM = "Робот-пылесос с влажной уборкой, самоочисткой швабр, станцией выгрузки мусора и голосовым управлением, белый, модель 2026 года"
LONG_CATEGORY = "Очень длинное название категории для проверки переносов и ровной высоты карточек на телефоне"
LONG_STORE = "Интернет-магазин бытовой техники и электроники «Супер-Мега-Маркет Плюс»"


@dataclass(frozen=True, slots=True)
class Seeded:
    admin: str
    user: str
    pages: dict[str, str]


@contextmanager
def live_site(engine: Engine, media: Path) -> Iterator[str]:
    """Сервер приложения на 127.0.0.1 со случайным портом; адрес вида http://127.0.0.1:PORT."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    settings = Settings(env="test", public_origin=origin, database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), HASHER, lambda: datetime.now(UTC), MediaStore(media), LinkCatalog())
    server = uvicorn.Server(uvicorn.Config(create_app(settings, context), host="127.0.0.1", port=port, log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("сервер для проверок интерфейса не запустился")
        time.sleep(0.05)
    try:
        yield origin
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def seed(engine: Engine) -> Seeded:
    """Администратор и обычный пользователь; товары во всех состояниях раздела 11 ТЗ. Логины уникальны на каждый вызов."""
    now = datetime.now(UTC)
    suffix = uuid.uuid4().hex[:8]
    with engine.begin() as db:
        world = _World(db, now)
        admin = world.user(f"ui-admin-{suffix}", "admin", "Владелец")
        user = world.user(f"ui-user-{suffix}", "user", "Анна")
        shop = world.store(LONG_STORE, "store-6")
        other = world.store("Ситилинк", "store-7")
        disabled = world.store("Магазин на ремонте", "store-8", enabled=False)

        states = world.category(admin, "Состояния интерфейса")
        long_item = world.item(admin, states, LONG_ITEM, full_name=LONG_ITEM + " — полное название из карточки магазина")
        world.history(world.link(admin, long_item, shop), [64_990_00, 62_490_00, 59_990_00])
        huge = world.item(admin, states, "Холодильник огромный", quantity=999)
        world.history(world.link(admin, huge, other), [9_999_999_00, 9_899_999_00])
        out = world.link(admin, world.item(admin, states, "Нет в наличии"), shop)
        world.history(out, [4_990_00])
        world.seen(out, now - DAY, availability="out_of_stock")
        stale_item = world.item(admin, states, "Цена не актуальна")
        stale = world.link(admin, stale_item, other)
        world.history(stale, [12_990_00, 11_990_00], start_days=20)
        for days in (6, 3, 1):
            world.seen(stale, now - days * DAY, status="TIMEOUT")
        world.history(world.link(admin, world.item(admin, states, "Устаревшая, но в итоге", include_stale=True), other), [7_490_00], start_days=12)
        awaiting = world.item(admin, states, "Ожидает проверки")
        world.link(admin, awaiting, shop, since=now - timedelta(minutes=5))
        no_links = world.item(admin, states, "Без ссылок, со старой историей")
        world.history(world.link(admin, no_links, other, since=now - 40 * DAY, until=now - 10 * DAY), [3_490_00, 3_290_00], start_days=35)
        world.history(world.link(admin, world.item(admin, states, "Цена в отключённом магазине"), disabled), [1_990_00])
        pending_item = world.item(admin, states, "Подозрительная цена")
        pending = world.link(admin, pending_item, shop, title="Чайник электрический")
        world.history(pending, [2_990_00, 2_890_00], start_days=6)
        world.seen(pending, now - timedelta(hours=2), 289_00, confirmation="pending", title="Чехол для чайника")
        purchased = world.item(admin, states, "Куплен недавно", state="purchased")
        world.history(world.link(admin, purchased, shop, since=now - 20 * DAY), [27_990_00, 24_990_00], start_days=15)
        archived = world.item(admin, states, "В архиве давно", state="archived")
        world.item(admin, states, "Кофемашина рожковая с капучинатором и автоматической очисткой", state="purchased")
        world.item(admin, states, "Пылесос вертикальный беспроводной с запасным аккумулятором и насадками", state="archived")

        long_category = world.category(admin, LONG_CATEGORY)
        world.history(world.link(admin, world.item(admin, long_category, "Лампа"), shop), [1_290_00, 1_390_00])
        many = world.category(admin, "Много товаров")
        shelf = world.group(admin, many, "Полка с очень длинным названием группы товаров")
        empty_group = world.group(admin, many, "Пустая группа")
        for index in range(1, 25):
            target = world.item(admin, many, f"Товар номер {index}", group_id=shelf if index <= 8 else None, in_total=index <= 16)
            world.history(world.link(admin, target, (shop, other)[index % 2]), [1000_00 + index * 37_00, 1000_00 + index * 31_00], start_days=10 + index % 7)
        empty_category = world.category(admin, "Пустая категория")
        for index in range(1, 5):
            small = world.category(admin, f"Категория {index}")
            # Свежие цены: товар в итоге, у карточки одна строка счётчика.
            world.history(world.link(admin, world.item(admin, small, f"Вещь {index}"), other), [500_00 * index, 480_00 * index], start_days=6)
            if index == 1:
                # Рядом карточки с «наблюдаю» и без: сумма и изменение должны стоять на одной высоте (снимок владельца 13.10.2026).
                world.history(world.link(admin, world.item(admin, small, "Наблюдаю рядом", in_total=False), other), [900_00, 880_00])
        watched = world.category(admin, "Только наблюдаю")
        for index in range(1, 13):
            world.history(world.link(admin, world.item(admin, watched, f"Наблюдаю {index}", in_total=False), other), [700_00, 690_00])

        home = world.category(user, "Дом")
        shared = world.link(user, world.item(user, home, "Чайник Анны"), shop, title="Чайник электрический")
        world.history(shared, [2_990_00, 2_890_00], start_days=6)
        world.seen(shared, now - timedelta(hours=2), 289_00, confirmation="pending", title="Чехол для чайника")

        pages = {
            "home": "/web/",
            "category-states": f"/web/categories/{states}",
            "category-long": f"/web/categories/{long_category}",
            "category-many": f"/web/categories/{many}",
            "category-empty": f"/web/categories/{empty_category}",
            "group": f"/web/groups/{shelf}",
            "group-empty": f"/web/groups/{empty_group}",
            "item-long": f"/web/items/{long_item}",
            "item-huge": f"/web/items/{huge}",
            "item-stale": f"/web/items/{stale_item}",
            "item-pending": f"/web/items/{pending_item}",
            "item-no-links": f"/web/items/{no_links}",
            "item-awaiting": f"/web/items/{awaiting}",
            "item-purchased": f"/web/items/{purchased}",
            "item-archived": f"/web/items/{archived}",
            "purchases": "/web/purchases",
            "archive": "/web/purchases/archive",
            "settings-items": "/web/settings/items",
            "settings-categories": "/web/settings/categories",
            "settings-profile": "/web/settings/profile",
            "settings-stores": "/web/settings/stores",
            "settings-check": "/web/settings/check",
            "settings-users": "/web/settings/users",
        }
    return Seeded(f"ui-admin-{suffix}", f"ui-user-{suffix}", pages)


class _World:
    def __init__(self, db: Connection, now: datetime) -> None:
        self.db = db
        self.now = now
        self.run = self.one("INSERT INTO parse_runs (kind, status, total, created_at, finished_at) VALUES ('on_add', 'finished', 0, :t, :t) RETURNING id", t=now - 40 * DAY)
        self.positions: dict[object, int] = {}

    def one(self, sql: str, **params: object) -> int:
        return self.db.execute(text(sql), params).scalar_one()

    def _next(self, key: object) -> int:
        self.positions[key] = self.positions.get(key, -1) + 1
        return self.positions[key]

    def user(self, login: str, role: str, name: str) -> int:
        return self.one("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, :h, :r, :n) RETURNING id", l=login, h=HASHER.hash(PASSWORD), r=role, n=name)

    def store(self, name: str, color: str, enabled: bool = True) -> int:
        return self.one(
            "INSERT INTO stores (canonical_host, display_name, color, parser_key, enabled, auto_created, min_delay_ms) VALUES (:h, :n, :c, 'universal', :e, true, 2000) RETURNING id",
            h=f"{uuid.uuid4().hex[:10]}.example", n=name, c=color, e=enabled,
        )

    def category(self, user: int, name: str) -> int:
        return self.one("INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, :n, lower(:n), :p) RETURNING id",
                        u=user, n=name, p=self._next(("category", user)))

    def group(self, user: int, category: int, name: str) -> int:
        return self.one("INSERT INTO groups (user_id, category_id, name, name_normalized, position) VALUES (:u, :c, :n, lower(:n), :p) RETURNING id",
                        u=user, c=category, n=name, p=self._next(("group", category)))

    def item(self, user: int, category: int, name: str, *, quantity: int = 1, state: str = "active", include_stale: bool = False,
             group_id: int | None = None, full_name: str | None = None, in_total: bool = True) -> int:
        return self.one(
            "INSERT INTO items (user_id, category_id, group_id, name, full_name, quantity, in_total, include_stale, state, purchased_at, purchase_price_minor, "
            "purchase_quantity, archived_at, position) VALUES (:u, :c, :g, :n, :f, :q, :it, :s, :st, CASE WHEN :st = 'purchased' THEN :at END, "
            "CASE WHEN :st = 'purchased' THEN 2500000 END, CASE WHEN :st = 'purchased' THEN 1 END, CASE WHEN :st = 'archived' THEN :at END, :p) RETURNING id",
            u=user, c=category, g=group_id, n=name, f=full_name, q=quantity, it=in_total, s=include_stale, st=state, at=self.now - 3 * DAY, p=self._next(("item", category)),
        )

    def link(self, user: int, item: int, store: int, *, since: datetime | None = None, until: datetime | None = None, title: str = "Товар") -> int:
        link = self.one("INSERT INTO tracked_links (store_id, normalized_url, region_code, baseline_title) VALUES (:s, :u, 'spb', :t) RETURNING id",
                        s=store, u=f"https://shop.example/{uuid.uuid4().hex}", t=title)
        self.one("INSERT INTO item_links (user_id, item_id, tracked_link_id, store_id, valid_from, valid_to) VALUES (:u, :i, :l, :s, :f, :t) RETURNING id",
                 u=user, i=item, l=link, s=store, f=since or self.now - 30 * DAY, t=until)
        return link

    def seen(self, link: int, at: datetime, price: int | None = None, *, availability: str = "in_stock", status: str = "ok",
             confirmation: str = "confirmed", title: str = "Товар") -> None:
        task = self.one(
            "INSERT INTO check_tasks (run_id, tracked_link_id, status, error_code, finished_at, created_at) "
            "VALUES (:r, :l, CASE WHEN :s = 'ok' THEN 'done' ELSE 'failed' END, CASE WHEN :s = 'ok' THEN NULL ELSE :s END, :t, :t) RETURNING id",
            r=self.run, l=link, s=status, t=at,
        )
        self.one(
            "INSERT INTO price_observations (tracked_link_id, task_id, checked_at, status, price_minor, availability, title, region_code, confirmation, source) "
            "VALUES (:l, :k, :t, :s, :p, :a, CASE WHEN :s = 'ok' THEN :ti END, 'spb', :c, CASE WHEN :s = 'ok' THEN 'site_data' END) RETURNING id",
            l=link, k=task, t=at, s=status, p=price, a=availability, ti=title, c=confirmation,
        )

    def history(self, link: int, prices: list[int], start_days: int = 25) -> None:
        for index, price in enumerate(prices):
            self.seen(link, self.now - (start_days - index * start_days / len(prices)) * DAY, price)


# Проверки раздела 12 SITE_DESIGN_SPEC.md прямо в странице: список нарушений с описанием элемента.
# Содержимое закрытого <details> и закрытых диалогов не видно и не проверяется.
PAGE_CHECKS_JS = r"""
() => {
  const problems = [];
  const describe = (el) => {
    let s = el.tagName.toLowerCase();
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (cls.length) s += '.' + cls.join('.');
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return t ? `${s} «${t}»` : s;
  };
  const visible = (el) => {
    const folded = el.closest('details:not([open])');
    if (folded && !el.closest('summary')) return false;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    // Текст только для скринридера (блок 1×1 с обрезкой) на экране не виден.
    if (r.width <= 1 && r.height <= 1 && cs.overflow === 'hidden') return false;
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && !el.closest('dialog:not([open]), [hidden], template');
  };
  const width = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > width + 1) problems.push(`страница шире экрана: ${document.documentElement.scrollWidth} > ${width}`);
  const all = [...document.querySelectorAll('body *')].filter(visible);
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (el.children.length === 0 && (el.innerText || '').trim() && el.scrollWidth > el.clientWidth + 1 && cs.overflowX === 'visible' && cs.display !== 'inline')
      problems.push('текст вылезает из блока: ' + describe(el));
    // Текст не заходит на внутреннее поле блока с отступом (карточки): правый край — до границы содержимого.
    if ([...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) && !el.closest('[style*="position"], .bell__badge') && cs.position === 'static') {
      for (let box = el.parentElement; box && box !== document.body; box = box.parentElement) {
        if (['auto', 'scroll'].includes(getComputedStyle(box).overflowX)) break;  // прокручиваемая полоса — задумано
        const pad = parseFloat(getComputedStyle(box).paddingRight);
        if (pad > 0) {
          const limit = box.getBoundingClientRect().right - pad;
          if (el.getBoundingClientRect().right > limit + 1) problems.push('текст заходит на поле блока: ' + describe(el));
          break;
        }
      }
    }
    // Многоточие допустимо, только если полный текст где-то доступен (подсказка title).
    if (cs.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1 && !el.closest('[title]'))
      problems.push('обрезан многоточием без полного текста: ' + describe(el));
    if (cs.cursor === 'pointer' && !el.closest('a,button,label,summary,input,select,textarea') && el.matches('div,span,li,td,tr,section,article'))
      problems.push('курсор-рука не на кнопке или ссылке: ' + describe(el));
  }
  for (const el of document.querySelectorAll('[hx-get],[hx-post]')) {
    const trigger = el.getAttribute('hx-trigger') || '';
    if (!el.matches('a,button,form,input,select,textarea') && !/load|revealed|every|intersect/.test(trigger)) problems.push('htmx не на кнопке или форме: ' + describe(el));
  }
  // Цветная точка магазина стоит на первой строке его названия, а не отдельной строкой над ним.
  for (const dot of document.querySelectorAll('.store-dot')) {
    if (!visible(dot)) continue;
    let node = dot.nextSibling;
    while (node && node.nodeType === 3 && !node.textContent.trim()) node = node.nextSibling;
    if (!node) continue;
    const range = document.createRange(); range.selectNodeContents(node);
    const first = [...range.getClientRects()].find(r => r.width > 0);
    const d = dot.getBoundingClientRect(); const middle = d.top + d.height / 2;
    if (first && (middle < first.top - 1 || middle > first.bottom + 1)) problems.push('точка магазина оторвана от названия: ' + (node.textContent || '').trim().slice(0, 40));
  }
  const name = (el) => (el.getAttribute('aria-label') || '').trim() || (el.innerText || '').trim() || (el.getAttribute('title') || '').trim()
    || (el.getAttribute('aria-labelledby') || '').split(' ').map(id => document.getElementById(id)?.innerText || '').join(' ').trim();
  for (const el of document.querySelectorAll('a[href], button, [role=button]')) if (visible(el) && !name(el)) problems.push('без подписи: ' + el.outerHTML.slice(0, 100));
  for (const el of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
    if (visible(el) && !(el.labels?.length || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby'))) problems.push('поле без подписи: ' + describe(el) + ' name=' + el.name);
  }
  for (const img of document.querySelectorAll('img')) if (visible(img) && !img.hasAttribute('alt')) problems.push('картинка без alt: ' + img.src.slice(-40));
  for (const svg of document.querySelectorAll('svg')) {
    if (visible(svg) && svg.getAttribute('aria-hidden') !== 'true' && !svg.getAttribute('aria-label') && !svg.getAttribute('role') && !svg.querySelector('title'))
      problems.push('svg без подписи и не скрыт: ' + describe(svg.parentElement));
  }
  // Контраст текста — WCAG AA: 4,5, у крупного (от 24 px или от 18,66 px жирным) — 3. Фон — первый непрозрачный слой под текстом.
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const v = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return {r: v[0], g: v[1], b: v[2], a: v.length > 3 ? v[3] : 1}; };
  const lum = ({r, g, b}) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  // Фон — слои, которые на экране лежат под серединой текста (в том числе соседние подложки вроде бегунка
  // переключателя); вне экрана — родители. Градиент под текстом — контраст не оценивается.
  const backdrop = (el) => {
    const layers = [];
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const onScreen = cx >= 0 && cy >= 0 && cx < innerWidth && cy < innerHeight;
    const stack = onScreen ? document.elementsFromPoint(cx, cy).filter(a => a === el || !el.contains(a)) : [];
    const covers = (a) => { const b = a.getBoundingClientRect(); return b.left <= cx && cx <= b.right && b.top <= cy && cy <= b.bottom; };
    const offScreenChain = () => {
      const up = [];
      for (let a = el; a; a = a.parentElement) {
        up.push(a);
        for (const sibling of a.parentElement ? a.parentElement.children : []) {
          if (sibling !== a && ['absolute', 'fixed'].includes(getComputedStyle(sibling).position) && covers(sibling)) up.push(sibling);
        }
      }
      return up;
    };
    const chain = stack.length ? stack : offScreenChain();
    for (const a of chain) {
      const cs = getComputedStyle(a);
      if (cs.backgroundImage !== 'none') return null;
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let base = {r: 255, g: 255, b: 255};
    for (const c of layers.reverse()) base = {r: c.r * c.a + base.r * (1 - c.a), g: c.g * c.a + base.g * (1 - c.a), b: c.b * c.a + base.b * (1 - c.a)};
    return base;
  };
  for (const el of all) {
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim()) || el.closest('[disabled], [aria-disabled=true]')) continue;
    const cs = getComputedStyle(el); const fg = parse(cs.color); const bg = backdrop(el);
    if (!fg || !bg) continue;
    const mixed = {r: fg.r * fg.a + bg.r * (1 - fg.a), g: fg.g * fg.a + bg.g * (1 - fg.a), b: fg.b * fg.a + bg.b * (1 - fg.a)};
    const [hi, lo] = [lum(mixed), lum(bg)].sort((x, y) => y - x);
    const ratio = (hi + 0.05) / (lo + 0.05);
    const size = parseFloat(cs.fontSize);
    const need = size >= 24 || (parseInt(cs.fontWeight) >= 700 && size >= 18.66) ? 3 : 4.5;
    if (ratio < need) problems.push(`низкий контраст ${ratio.toFixed(2)}: ` + describe(el));
  }
  // Телефон: цели нажатия не меньше 24×24 (WCAG 2.5.8), шапка в одну строку.
  if (width < 768) {
    for (const el of document.querySelectorAll('a[href], button, input:not([type=hidden]), select, summary')) {
      if (!visible(el) || el.closest('p')) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 24 || r.height < 24) problems.push(`мелкая цель ${Math.round(r.width)}×${Math.round(r.height)}: ` + describe(el));
    }
    const header = document.querySelector('header.top');
    if (header && header.getBoundingClientRect().height > 64) problems.push(`шапка выше 64 px: ${Math.round(header.getBoundingClientRect().height)}`);
  }
  const h1 = document.querySelector('h1');
  if (h1 && visible(h1)) {
    const r = h1.getBoundingClientRect();
    const at = document.elementFromPoint(Math.min(document.documentElement.clientWidth - 2, r.left + 4), r.top + r.height / 2);
    if (at && at !== h1 && !h1.contains(at)) problems.push('заголовок закрыт: ' + describe(at));
  }
  return problems;
}
"""

# Элементы, у которых при «уменьшить движение» остались переходы или анимации дольше 20 мс.
MOTION_JS = r"""
() => [...document.querySelectorAll('body *')].filter(el => {
  const cs = getComputedStyle(el);
  const longest = (v) => Math.max(...v.split(',').map(x => parseFloat(x) * (x.trim().endsWith('ms') ? 0.001 : 1)));
  return longest(cs.transitionDuration) > 0.02 || (cs.animationName !== 'none' && longest(cs.animationDuration) > 0.02);
}).slice(0, 10).map(el => el.tagName.toLowerCase() + '.' + (el.getAttribute('class') || '').split(' ')[0])
"""
