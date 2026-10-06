"""Цена «глазами» — для сайтов без данных для поисковиков (DEVELOPMENT_ARCHITECTURE.md, раздел 7).

Подход — из рабочего парсера владельца (prise-bot, visual.py), переписан под правила проекта.
Скрипт выполняется в открытой странице и собирает видимые ценники вида «64 999 ₽» с размером
шрифта, жирностью и расстоянием до заголовка товара (h1). Человек находит цену так же: самое
крупное число с ₽ рядом с названием. Отбрасываются зачёркнутые цены, «в месяц», рассрочка,
бонусы, кэшбэк и ценники далеко ниже заголовка (там «похожие товары»). Наличие — по кнопкам
«В корзину» и «Нет в наличии» в верхней части карточки. Фото — самая крупная картинка рядом.
Деньги — в копейках, через тот же строгий разбор, что и для данных сайта.
"""

from dataclasses import dataclass
from typing import Any

from pricewatch.domain.pricing import Availability
from pricewatch.parsers.prices import structured_price

VISUAL_JS = r"""
() => {
  const PRICE = /^\s*(?:от\s*)?(\d{1,3}(?:[\s  ]\d{3})+|\d{1,7})((?:[.,]\d{1,2})?)\s*(?:₽|руб\.?|р\.)\s*$/i;
  const BAD = /мес|кредит|рассроч|долями|сплит|split|бонус|балл|кэшб|кешб|cashback|выгод|экономи|за шт|\/\s*шт|доставк/i;
  const h1 = document.querySelector('h1');
  const h1r = h1 ? h1.getBoundingClientRect() : null;
  const h1y = h1r ? h1r.top + scrollY : 0;
  const text = (el) => (el.textContent || '').replace(/\s+/g, ' ').trim();
  const cands = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (el.children.length > 6) continue;
    const t = text(el);
    if (t.length > 26) continue;
    const m = t.match(PRICE);
    if (!m) continue;
    // самый глубокий элемент, в котором целиком лежит ценник
    if ([...el.children].some((c) => PRICE.test(text(c)))) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    let struck = false, ctx = '';
    for (let a = el, k = 0; a && k < 4; a = a.parentElement, k++) {
      const s = getComputedStyle(a);
      if ((s.textDecorationLine || '').includes('line-through') || a.tagName === 'S' || a.tagName === 'DEL') struck = true;
      // Контекст — только небольшой блок вокруг цены: текст всей страницы содержит «доставку» и «рассрочку» всегда.
      const around = text(a);
      if (k <= 2 && around.length <= 140) ctx = around;
    }
    const y = r.top + scrollY;
    cands.push({
      amount: m[1] + m[2], size: parseFloat(cs.fontSize) || 0, weight: +cs.fontWeight || 400,
      dy: Math.round(Math.abs(y - h1y)), dx: Math.round(h1r ? Math.abs(r.left - h1r.left) : r.left),
      struck, bad: BAD.test(ctx),
    });
    if (cands.length > 80) break;
  }
  const BUY = /^(в корзину|купить|добавить в корзину|купить сейчас|купить в 1 клик|оформить|заказать|положить в корзину)$/i;
  const OOS = /(нет в наличии|сообщить о поступлении|товар закончился|нет в продаже|снят с продажи|распродан|товар недоступен|ожидается поступление|узнать о поступлении|временно отсутствует|закончился)/i;
  let buy = false, oos = false;
  for (const b of document.querySelectorAll('button, a, [role=button], div, span')) {
    if (b.children.length > 3) continue;
    const t = text(b);
    if (!t || t.length > 40) continue;
    const r = b.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.top + scrollY > h1y + 1400) continue;
    if (BUY.test(t) && !b.disabled && b.getAttribute('aria-disabled') !== 'true') buy = true;
    if (OOS.test(t)) oos = true;
  }
  let image = null, best = 0;
  for (const img of document.images) {
    const r = img.getBoundingClientRect();
    const area = r.width * r.height;
    if (r.width < 150 || r.height < 150 || r.top + scrollY > h1y + 900 || r.width / r.height > 3) continue;
    if (area > best) { best = area; image = img.currentSrc || img.src; }
  }
  return {cands, buy, oos, image, h1: h1 ? text(h1).slice(0, 300) : null};
}
"""

MAX_DISTANCE_FROM_TITLE = 1500


@dataclass(frozen=True, slots=True)
class VisualPrice:
    price_minor: int
    size: float
    bold: bool


def pick_price(probe: Any) -> VisualPrice | None:
    """Главный ценник: крупный, жирный, рядом с заголовком, не зачёркнутый."""
    if not isinstance(probe, dict) or not probe.get("h1"):
        return None  # без заголовка товара не понять, чья это цена
    best: tuple[float, VisualPrice] | None = None
    for candidate in probe.get("cands") or []:
        if not isinstance(candidate, dict) or candidate.get("struck") or candidate.get("bad"):
            continue
        dy, dx = _number(candidate.get("dy")), _number(candidate.get("dx"))
        if dy is None or dx is None or dy >= MAX_DISTANCE_FROM_TITLE:
            continue
        try:
            price = structured_price(str(candidate.get("amount", "")))
        except ValueError:
            continue
        size = _number(candidate.get("size")) or 0.0
        bold = (_number(candidate.get("weight")) or 400) >= 600
        score = size * 3 + (6 if bold else 0) - dy / 60 - dx / 200
        if best is None or score > best[0]:
            best = (score, VisualPrice(price, size, bold))
    return best[1] if best else None


def availability(probe: Any) -> Availability:
    """По кнопкам: «В корзину» — есть, «Нет в наличии» — нет, иначе неизвестно."""
    if not isinstance(probe, dict):
        return Availability.UNKNOWN
    if probe.get("buy"):
        return Availability.IN_STOCK
    if probe.get("oos"):
        return Availability.OUT_OF_STOCK
    return Availability.UNKNOWN


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
