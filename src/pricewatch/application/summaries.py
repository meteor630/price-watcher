"""Сводка проверки пользователю (DEVELOPMENT_ARCHITECTURE.md, раздел 10): после завершения планового или ручного
запуска каждому пользователю с привязанным Telegram — одно сообщение «изменения есть» или «изменений нет».

- Изменение — засчитанная этому пользователю смена действующей цены или наличия у ссылки его товара
  («в списке» или купленного, пока он проверяется) за время запуска относительно предыдущего засчитанного
  наблюдения (domain.pricing.changed_between). Подозрительное считается по решениям самого пользователя.
- Сводки создаёт планировщик на обходе: запуск завершается в разных местах (результат worker, остановка,
  отмена, истёкшая аренда), а здесь — одно место. Сообщения и отметка «сводка создана» — в одной транзакции:
  запуск не получит двух сводок, даже если планировщиков два. Запуск on_add сводки не создаёт.
"""

from datetime import datetime

from pricewatch.application.ports import FinishedRun, UnitOfWork
from pricewatch.application.price_view import seen_by_user
from pricewatch.domain.pricing import changed_between
from pricewatch.domain.shopping import ItemState

RUNS_PER_TICK = 5
SUMMARY_KIND = "run_summary"
WATCHED_STATES = (ItemState.ACTIVE, ItemState.PURCHASED)


def send_run_summaries(uow: UnitOfWork, now: datetime) -> int:
    """Создать сводки по завершённым запускам; сколько запусков обработано."""
    with uow:
        runs = uow.queue.runs_to_summarize(RUNS_PER_TICK)
        for run in runs:
            for user in uow.telegram.linked_users():
                changed = _changed_for_user(uow, user.id, run)
                uow.notifications.notify_telegram(user.id, SUMMARY_KIND, f"{SUMMARY_KIND}:{run.run_id}", {"run_id": run.run_id, "changed": changed}, now)
            uow.queue.mark_summarized(run.run_id, now)
        uow.commit()
    return len(runs)


def _changed_for_user(uow: UnitOfWork, user_id: int, run: FinishedRun) -> bool:
    watched = {item.item_id for item in uow.price_data.items(user_id) if item.state in WATCHED_STATES}
    every_binding = uow.price_data.bindings(user_id)
    link_ids = sorted({
        binding.tracked_link_id for binding in every_binding
        if binding.item_id in watched and binding.valid_from <= run.finished_at and (binding.valid_to is None or binding.valid_to > run.created_at)
    })
    if not link_ids:
        return False
    history = seen_by_user(uow, user_id, link_ids, every_binding, run.created_at)
    return any(changed_between(history[link_id], run.created_at, run.finished_at) for link_id in link_ids)
