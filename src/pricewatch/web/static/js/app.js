/*
 * Поведение сайта: переключатель периода и раскрывающиеся панели главной.
 * Без встроенного кода и eval (CSP). Хуки — data-* атрибуты; запросы делает htmx,
 * здесь — только состояние кнопок и открытие панели. Анимации — в CSS, с учётом reduced motion.
 */
(() => {
  "use strict";

  // ---------- переключатель периода ----------
  // Подложка едет сразу по нажатию; если запрос не удался, она возвращается назад.

  function choose(segmented, option) {
    segmented.dataset.active = option.dataset.segOption;
    for (const other of segmented.querySelectorAll("[data-seg-option]")) {
      if (other === option) {
        other.setAttribute("aria-current", "true");
      } else {
        other.removeAttribute("aria-current");
      }
    }
  }

  document.addEventListener("click", (event) => {
    const option = event.target.closest("[data-seg-option]");
    if (!option) {
      return;
    }
    const segmented = option.closest("[data-segmented]");
    const current = segmented.querySelector('[aria-current="true"]');
    segmented.previousOption = current;
    choose(segmented, option);
  });

  document.addEventListener("htmx:responseError", (event) => {
    const segmented = event.detail.elt.closest("[data-segmented]");
    if (segmented && segmented.previousOption) {
      choose(segmented, segmented.previousOption);
    }
  });

  // ---------- сводные панели ----------
  // Открыта не больше одной; повторное нажатие, «Свернуть» и Esc закрывают её.

  function expanded() {
    return document.querySelector('[data-panel][aria-expanded="true"]');
  }

  function closeDrawer() {
    const drawer = document.querySelector("[data-drawer]");
    const open = expanded();
    if (drawer) {
      drawer.classList.remove("is-open");
    }
    if (open) {
      open.setAttribute("aria-expanded", "false");
      open.focus();
    }
  }

  document.addEventListener("htmx:confirm", (event) => {
    const button = event.detail.elt;
    if (button.matches("[data-panel]") && button.getAttribute("aria-expanded") === "true") {
      event.preventDefault();
      closeDrawer();
    }
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    const button = event.detail.requestConfig && event.detail.requestConfig.elt;
    if (!button || !button.matches("[data-panel]")) {
      return;
    }
    for (const other of document.querySelectorAll("[data-panel]")) {
      other.setAttribute("aria-expanded", other === button ? "true" : "false");
    }
    document.querySelector("[data-drawer]").classList.add("is-open");
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-drawer-close]")) {
      closeDrawer();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && expanded()) {
      closeDrawer();
    }
  });
})();
