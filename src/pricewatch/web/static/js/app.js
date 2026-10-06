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

/*
 * Подсказка большого графика: при наведении или касании показывает готовую подпись дня
 * («5 окт · 873 010 ₽») и точку на линии. Подписи и координаты приходят с сервера в data-*.
 */
(() => {
  "use strict";

  function hide(figure) {
    figure.querySelector("[data-chart-tip]").hidden = true;
    figure.querySelector("[data-chart-focus]").classList.remove("is-on");
  }

  function show(figure, hit) {
    const tip = figure.querySelector("[data-chart-tip]");
    const focus = figure.querySelector("[data-chart-focus]");
    const center = (Number(hit.getAttribute("x")) + Number(hit.getAttribute("width")) / 2) / 10;
    tip.textContent = hit.dataset.label;
    tip.style.left = `${center}%`;
    tip.hidden = false;
    if (hit.dataset.y) {
      focus.setAttribute("cx", `${center}%`);
      focus.setAttribute("cy", hit.dataset.y);
      focus.classList.add("is-on");
    } else {
      focus.classList.remove("is-on");
    }
  }

  function onPointer(event) {
    const hit = event.target.closest(".bigchart__hit");
    if (hit) {
      show(hit.closest("[data-chart]"), hit);
    }
  }

  document.addEventListener("pointermove", onPointer);
  document.addEventListener("pointerdown", onPointer);
  document.addEventListener("pointerout", (event) => {
    const figure = event.target.closest && event.target.closest("[data-chart]");
    if (figure && !figure.contains(event.relatedTarget)) {
      hide(figure);
    }
  });
})();

/*
 * Перетаскивание (SortableJS), окна подтверждения, сообщения с «Отменить», автофокус.
 * Порядок после перетаскивания пишется в скрытую форму [data-sort-form], отправляет её htmx —
 * тот же путь, что у кнопок «выше/ниже». На телефоне перетаскивание начинается после удержания,
 * поэтому обычная прокрутка не мешает.
 */
(() => {
  "use strict";

  const TOAST_MS = 3000;
  const HOLD_MS = 350;

  function initSortables(root) {
    for (const list of root.querySelectorAll("[data-sortable]")) {
      if (list.sortable) {
        continue;
      }
      const form = document.getElementById(list.dataset.sortable);
      list.sortable = Sortable.create(list, {
        animation: 180,
        handle: list.dataset.sortHandle || undefined,
        delay: HOLD_MS,
        delayOnTouchOnly: true,
        ghostClass: "is-ghost",
        chosenClass: "is-chosen",
        onEnd(event) {
          if (event.oldIndex === event.newIndex) {
            return;
          }
          form.elements.ids.value = list.sortable.toArray().join(",");
          if (navigator.vibrate) {
            navigator.vibrate(10);
          }
          htmx.trigger(form, "submit");
        },
      });
    }
  }

  function armToasts(root) {
    for (const toast of root.querySelectorAll("[data-toast]")) {
      if (toast.armed) {
        continue;
      }
      toast.armed = true;
      setTimeout(() => {
        // Нажатие или фокус на «Отменить» не должны пропасть вместе с сообщением.
        if (toast.contains(document.activeElement)) {
          toast.addEventListener("focusout", () => toast.remove(), { once: true });
        } else {
          toast.remove();
        }
      }, TOAST_MS);
    }
  }

  function focusFirst(root) {
    const field = root.querySelector("[data-autofocus]");
    if (field) {
      field.focus();
      if (field.select) {
        field.select();
      }
    }
  }

  function closeDialog(dialog) {
    if (dialog.open) {
      dialog.close();
    }
    dialog.remove();
  }

  function openDialogs(root) {
    const dialogs = root.matches && root.matches("dialog[data-open]") ? [root] : root.querySelectorAll("dialog[data-open]");
    for (const dialog of dialogs) {
      if (dialog.open || !dialog.isConnected) {
        continue;
      }
      // Esc закрывает окно средствами браузера; после этого оно больше не нужно.
      dialog.addEventListener("close", () => dialog.remove(), { once: true });
      dialog.showModal();
      focusFirst(dialog);
    }
  }

  htmx.onLoad((root) => {
    initSortables(root);
    armToasts(root);
    openDialogs(root);
  });

  document.addEventListener("htmx:afterSwap", (event) => {
    focusFirst(event.detail.target);
  });

  document.addEventListener("htmx:afterRequest", (event) => {
    // Закрывается только форма, которая это просит: окно нового товара после сохранения остаётся открытым.
    const form = event.detail.elt.closest && event.detail.elt.closest("[data-close-on-success]");
    const dialog = form && form.closest("dialog[data-modal]");
    if (dialog && event.detail.successful) {
      closeDialog(dialog);
    }
  });

  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      closeDialog(closer.closest("dialog"));
    }
  });
})();
