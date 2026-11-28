# Карта проекта

Читается второй после `AGENTS.md`. Только короткие пункты и пути, без кода и секретов. Обновляется в той же задаче, что меняет модули, связи, данные, API, конфигурацию или команды тестов.

Проверка: `uv run python tools/check_project_map.py`. Формат, который она понимает, описан в начале `tools/check_project_map.py`. Пути пишутся в строках «Путь», «Файлы», «Тесты», «Команда». В строках «Путь» и «Файлы» в обратных кавычках — только пути.

## Команды

- Команда: `uv sync` — установить окружение.
- Команда: `uv run python -m pricewatch.bootstrap` — запустить сервер; настройки из окружения и .env; без базы или со старой схемой не стартует.
- Команда: `uv run python -m pricewatch.bootstrap scheduler` — запустить планировщик: плановые проверки по расписанию, возврат просроченных заданий; настройки как у сервера.
- Команда: `uv run python -m pricewatch.worker` — запустить worker; настройки из окружения и worker.env.
- Команда: `uv run python -m pricewatch.bot` — запустить Telegram-бота; настройки из окружения и bot.env.
- Команда: `uv run alembic upgrade head` — применить миграции; адрес базы из настроек.
- Команда: `uv run python -m pricewatch.bootstrap create-admin --login <логин> --name <имя>` — создать администратора; пароль спросит дважды без эха (для автоматизации — --password-stdin).
- Команда: `TEST_DATABASE_URL=postgresql://localhost/postgres uv run pytest` — весь набор тестов; без TEST_DATABASE_URL тесты базы падают с объяснением.
- Команда: `LC_ALL=en_US.UTF-8 pg_ctl -D /opt/homebrew/var/postgresql@18 -l /opt/homebrew/var/log/postgresql@18.log start` — запустить локальный PostgreSQL 18 (Homebrew) до перезагрузки.
- Команда: `uv run lint-imports` — границы модулей.
- Команда: `uv run python tools/check_project_map.py` — карта против кода.
- Команда: `uv run python tools/deps.py <модуль>` — прямые зависимые модуля.

## Документы

- Файлы: `AGENTS.md` — правила работы.
- Файлы: `PROJECT_PLAN_USER.md` — что делает сайт для пользователя.
- Файлы: `DEVELOPMENT_ARCHITECTURE.md` — архитектура и этапы.
- Файлы: `SITE_DESIGN_SPEC.md` — интерфейс сайта.
- Файлы: `IOS_APP_PLAN.md` — приложение iPhone и iPad.
- Файлы: `FEATURE_STATUS.md` — состояние функций.

Какие разделы читать под задачу (номера разделов `DEVELOPMENT_ARCHITECTURE.md`, если не указано иное):

| Задача | Разделы |
|---|---|
| Границы модулей, новый пакет | 3 |
| Вход, роли, токены | 4; `SITE_DESIGN_SPEC.md` 14 |
| Схема базы, миграции, конфликты версий | 5 |
| Расчёт цен, итогов, периодов | 6 |
| Парсер, адаптер магазина, SSRF, картинки | 7 |
| Очередь, запуски, расписание, worker | 8 |
| API, HTML-фрагменты | 9; `SITE_DESIGN_SPEC.md` 13 |
| Ошибки, логи, уведомления | 10 |
| Резервные копии | 11 |
| Узлы, туннель, деплой | 1, 12 (этап 11) |
| Страница сайта | `SITE_DESIGN_SPEC.md`: раздел страницы, 2, 10–12 |
| Приложение iOS | `IOS_APP_PLAN.md` |

## Конфигурация

- Файлы: `pyproject.toml` — проект, зависимости, pytest (pythonpath: tests/integration и tests/security — общие помощники тестов), контракты import-linter.
- Файлы: `uv.lock` — точные версии.
- Файлы: `alembic.ini` — расположение миграций; адреса базы в нём нет.
- Файлы: `.python-version` — Python 3.13.
- Файлы: `.gitignore`
- Файлы: `.env.example` — все настройки с пояснениями, без секретов; копируется в .env. PRICEWATCH_SECRET_KEY — ключ HMAC для кодов, токенов, сессий; его смена разлогинивает всех. PRICEWATCH_MEDIA_DIR — каталог фото, входит в резервную копию.
- Файлы: `worker.env.example` — настройки worker без токена; копируется в worker.env (в Git не попадает, как и профили браузера .browser-profiles).
- Файлы: `bot.env.example` — настройки Telegram-бота без токенов; копируется в bot.env (в Git не попадает).
- Файлы: `backup.env.example` — настройки копий на сервере: папка, открытый ключ age, время по Москве; копируется в backup.env (в Git не попадает); на сервере заданы в deploy/vps/compose.yaml.
- Файлы: `pull.env.example` — настройки домашнего приёмника копий без токена: адрес сервера, папка, лимит размера, время; копируется в pull.env (в Git не попадает).
- Файлы: `.github/workflows/ci.yml` — CI на каждый push: тесты, import-linter, проверка карты на Linux и на Windows; на Windows — ещё разбор скриптов deploy/windows в Windows PowerShell 5.1 и установка worker (PostgreSQL 18 сборки EDB); сборка образов Docker сервера, проверка Caddyfile, разбор compose и setup.sh, запуск базы и сайта, /healthz, резервная копия образом планировщика, токен приёмника, забор копии по /backups/v1 приёмником с проверкой sha256 и отметкой получения, расшифровка тестовым ключом и pg_restore; разбор compose домашнего приёмника; gitleaks по всей истории. Действия закреплены по хешу, gitleaks и PostgreSQL для Windows — с проверкой контрольной суммы.
- Файлы: `.gitleaksignore` — ложные срабатывания gitleaks по точному отпечатку; только ненастоящие значения из тестов и только с согласия владельца.
- Файлы: `.dockerignore` — в образ попадают только pyproject.toml, uv.lock, alembic.ini и src: без секретов, тестов и локальных данных.
- Файлы: `deploy/Dockerfile` — образы: app (сайт и API, планировщик, миграции, команды; amd64 и arm64) и backup (плюс pg_dump 18 из репозитория PostgreSQL с проверкой ключа по отпечатку и age из Debian; по умолчанию scheduler --backups); папки /data/media и /backups принадлежат пользователю pricewatch; базовые образы по отпечатку, зависимости по uv.lock без dev, пользователь без прав root.
- Файлы: `deploy/home/compose.yaml` — домашний узел: только приёмник копий backup-pull (образ app, pull-backups; настройки ../../pull.env, копии — в BACKUP_HOST_DIR); без прав Linux, без повышения прав, образ только для чтения, 128 МБ, журналы 10 МБ × 3; входящих портов нет. Worker с Chrome — программой Windows (deploy/windows).
- Файлы: `deploy/local/compose.yaml`, `deploy/local/compose.lan.yaml`, `deploy/local/.env.example` — локальный Docker Compose для iPhone и симулятора: PostgreSQL, миграции, API и планировщик; публикация на IP домашней сети через дополнительный файл.
- Файлы: `deploy/home/.env.example` — папка копий дома (BACKUP_HOST_DIR) для Compose; копируется в deploy/home/.env (в Git не попадает).
- Файлы: `deploy/windows/install-worker.ps1`, `deploy/windows/start-worker.ps1`, `deploy/windows/autostart.ps1` — worker программой Windows: установка по uv.lock без dev и проверка Chrome и worker.env (значения не печатаются); запуск из корня с журналом logs\worker.log (10 МБ → .1), перезапуск через 30 с при сбое, без перезапуска при кодах 0, 2, 4, отказ при уже запущенном worker; задача Планировщика при входе в Windows без прав администратора, -Status, -Stop (вместе с Chrome профилей), -Remove. UTF-8 с BOM для Windows PowerShell 5.1.
- Файлы: `deploy/windows/restore-check.ps1` — ежемесячная проверка восстановления вручную: копия расшифровывается ключом age владельца и восстанавливается во временный PostgreSQL без сети и томов; печатает число товаров, ссылок, цен и картинок; контейнер и временные файлы удаляются.
- Тесты: `uv run pytest tests/unit/test_deploy_windows.py` — кодировка и остановка на ошибках; разбор скриптов и установка — в CI на Windows.
- Файлы: `deploy/vps/compose.yaml`, `deploy/vps/Caddyfile`, `deploy/vps/up.sh` — арендный сервер (Aeza, prices.meteor630.ru): PostgreSQL 18 (сеть backend без интернета, видна только сайту и планировщику), миграции, сайт с /healthz (отдаёт копии приёмнику — том backups только на чтение), планировщик с ежедневной копией (образ backup, том backups, картинки только на чтение), Caddy на 443 (TLS-ALPN-01, порт 80 не нужен; сайт доверяет X-Forwarded-For только от 172.31.250.2); без прав Linux, без повышения прав, образ только для чтения, лимиты памяти, журналы 10 МБ × 3; секреты — /srv/secrets/pricewatch (compose.env, server.env, права 600). Код — /opt/price-watcher (публичный репозиторий). Worker — дома, ходит сюда по HTTPS /worker/v1.
- Файлы: `deploy/vps/setup.sh`, `deploy/vps/RUNBOOK.md` — сервер с нуля одной командой (Ubuntu 24.04+): обновления и автообновления безопасности, пользователь meteor, SSH только по ключу без root, брандмауэр (443 всем, 22 — с IP администратора), Docker, клон кода, секреты создаются на сервере; перенос базы и картинок с домашнего узла и откат — в RUNBOOK.
- Команда: `cd deploy/home && docker compose up -d --build` — запустить домашний приёмник копий; токен приёмника — на сервере `sudo docker compose --env-file /srv/secrets/pricewatch/compose.env exec -T app python -m pricewatch.bootstrap create-backup-reader --name home`.

Зависимости рабочего кода: pydantic-settings (MIT), fastapi (MIT), uvicorn (BSD-3), sqlalchemy (MIT), psycopg (LGPL-3.0, как неизменённая библиотека), alembic (MIT), argon2-cffi (MIT), tzdata (Apache-2.0), httpx2 (BSD-3), price-parser (BSD-3), patchright (Apache-2.0; нужен установленный Google Chrome, на Linux — Xvfb), pillow (MIT-CMU), jinja2 (BSD-3). В статике сайта: htmx 2.0.10 (0BSD), SortableJS 1.15.7 (MIT). Для разработки: pytest, import-linter, grimp, openapi-spec-validator.

## Пакеты

Направление зависимостей: `api → application → domain`; `persistence` реализует интерфейсы `application`; `bootstrap` собирает приложение. Правила — в разделе «Границы модулей».

### pricewatch.domain
- Путь: `src/pricewatch/domain/`
- Отвечает: сущности и правила расчётов; только стандартная библиотека
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_domain_backups.py tests/unit/test_domain_errors.py tests/unit/test_domain_names.py tests/unit/test_domain_shopping.py tests/unit/test_domain_tokens.py tests/unit/test_domain_accounts.py tests/unit/test_domain_money.py tests/unit/test_domain_days.py tests/unit/test_domain_pricing.py tests/unit/test_domain_totals.py tests/unit/test_domain_charts.py tests/unit/test_domain_review.py tests/unit/test_domain_purchases.py tests/unit/test_domain_schedule.py tests/unit/test_domain_store_pause.py tests/unit/test_domain_history.py tests/unit/test_domain_undo.py tests/unit/test_domain_client_errors.py`
- Файлы: `src/pricewatch/domain/errors.py` — коды ошибок, безопасные сообщения, базовое исключение PriceWatcherError.
- Файлы: `src/pricewatch/domain/backups.py` — имя копии по моменту UTC, возраст по имени; на сервере — две последние (surplus), место перед копией — последняя × 1,5 + 1 ГБ; дома — 30 суток; файл суммы <копия>.sha256; «нет удачной копии / дом не забирал копии больше 36 часов» (от последнего события, без него — от первой попытки или выдачи токена).
- Файлы: `src/pricewatch/domain/names.py` — очистка имён для показа, ключ сравнения (регистр, пробелы, «ё», NFKC), пределы длины и количества.
- Файлы: `src/pricewatch/domain/shopping.py` — неизменяемые записи Category, Group, Item; состояния товара ItemState; регион REGION_CODE; срок окончательной очистки удалённого (30 дней).
- Файлы: `src/pricewatch/domain/tokens.py` — случайные токены и коды связки, HMAC с назначением (в том числе UNDO и BACKUP_READER), вывод следующего токена из предыдущего.
- Файлы: `src/pricewatch/domain/accounts.py` — роли, User, правила логина и пароля (NFKC), отображаемое имя, срок хранения данных заблокированного (7 суток).
- Файлы: `src/pricewatch/domain/money.py` — целые копейки, строки «12 990 ₽», «−652 ₽», процент с одним знаком (половины вверх), изменение Change; разбор введённой суммы и вид суммы для поля ввода.
- Файлы: `src/pricewatch/domain/pricing.py` — наблюдение, действующая цена, участие ссылки (меньше 7 суток), статус ссылки и товара по порядку раздела 6, лучшая цена, «на проверке»; расчёт на любой момент (проверки отсортированы один раз, граница — двоичным поиском).
- Файлы: `src/pricewatch/domain/totals.py` — привязки ссылок [valid_from, valid_to), цена товара на момент, отметка «в итоге» при устаревшей цене, итог, изменение «только из-за цен», минимум периода.
- Файлы: `src/pricewatch/domain/charts.py` — точки по московским дням (7/30/365), график магазина (цена за штуку, продолжение при замене ссылки), график суммы по сегодняшнему составу с разрывами при неполном наборе цен.
- Файлы: `src/pricewatch/domain/review.py` — смена названия (схожесть ниже 85 %) и скачок цены (больше 30 %) точно, в целых числах; вердикт, автоматическое закрытие ценового подозрения, пороги; что засчитано конкретному пользователю по его решениям (каждый решает за свои товары); пороги из настроек с допустимыми диапазонами.
- Файлы: `src/pricewatch/domain/client_errors.py` — отчёт приложения: вырезать из текста адресные параметры, Bearer, чувствительные пары и строки-токены; лимит 20 в час, хранение 90 суток.
- Файлы: `src/pricewatch/domain/schedule.py` — расписание: времена «ЧЧ:ММ» (1–3, по умолчанию 09:00 по Москве), ключ слота, последний наступивший слот промежутка, момент следующего слота (UTC).
- Файлы: `src/pricewatch/domain/store_pause.py` — ступени паузы магазина: 403/429 — 15 мин, 1 ч, до следующего запуска; капча — 15 с, 60 с, до следующего запуска.
- Файлы: `src/pricewatch/domain/undo.py` — токен отмены удаления, покупки и архива: вид, номер, срок 15 с, HMAC с ключом и пользователем; без хранения; чужой и просроченный — NOT_FOUND.
- Файлы: `src/pricewatch/domain/history.py` — история ссылки: смена цены (по карте, если есть), нет в наличии и снова в наличии, сбой после успеха; неподтверждённое не в счёт.
- Файлы: `src/pricewatch/domain/purchases.py` — переходы «куплен / в архиве / в списке», проверка данных покупки, 14 суток проверки после покупки, «сейчас дешевле», цена в момент добавления, «потрачено» и экономия по периодам и категориям.
- Файлы: `src/pricewatch/domain/days.py` — сутки с 00:00 по Москве (zoneinfo + tzdata), начала периодов day/week/month/year (0/7/30/365 суток), подписи «5 окт».

### pricewatch.application
- Путь: `src/pricewatch/application/`
- Отвечает: сценарии и интерфейсы репозиториев
- Зависит от: `pricewatch.domain`
- Тесты: `uv run pytest tests/integration/test_backups.py tests/integration/test_backup_watch.py tests/security/test_backups_http.py tests/unit/test_passwords.py tests/integration/test_create_admin_command.py tests/integration/test_registration.py tests/integration/test_web_login.py tests/integration/test_app_auth.py tests/integration/test_add_link.py tests/integration/test_worker_protocol.py tests/integration/test_runs.py tests/integration/test_store_pauses.py tests/integration/test_retention.py tests/integration/test_dashboard.py tests/integration/test_category.py tests/integration/test_item.py tests/integration/test_idempotency.py tests/integration/test_shopping_edit.py tests/integration/test_items_edit.py tests/integration/test_purchases.py tests/integration/test_check_settings.py tests/integration/test_user_decisions.py tests/integration/test_reviews.py tests/integration/test_stores_admin.py tests/integration/test_users_admin.py tests/integration/test_notifications.py tests/integration/test_client_errors.py tests/integration/test_telegram.py tests/integration/test_watchdog.py tests/integration/test_run_summaries.py tests/security/test_health_http.py`
- Файлы: `src/pricewatch/application/ports.py` — интерфейсы репозиториев и единицы работы; общие правила владельца, версий, перестановки, удаления; LinkResolver — описание адреса от сборщика цен.
- Файлы: `src/pricewatch/application/worker_protocol.py` — протокол worker: токен (HMAC WORKER, ротация с перекрытием), аренда 2 мин, продление, результат как недоверенный ввод, правила подозрительных изменений, автозакрытие ценового подозрения, первое название — эталон; подтверждённая цена снимает «устаревшую цену в итоге»; блокировка и капча — пауза магазина по ступеням, задание ждёт без траты попытки; исчерпано — магазин отдыхает до следующего запуска, его ждущие задания закрываются ошибкой, уведомление store_paused администраторам; другой ответ снимает паузу; фото — по своему действующему заданию, путь записывается, если у ссылки его ещё нет.
- Файлы: `src/pricewatch/application/runs.py` — запуски: ручной (только администратор; второй — RUN_IN_PROGRESS с прогрессом) и плановый по слоту, какие ссылки входят, остановка, истёкшая аренда (возврат, после 3 попыток TIMEOUT в историю), времена расписания из настроек.
- Файлы: `src/pricewatch/application/check_settings.py` — настройки проверки (администратор): до трёх времён по Москве, срок актуальности цены 1–30 суток, пороги подозрительных изменений (схожесть названия 50–99 %, скачок цены 5–200 %); запись по версиям, ошибки по полям, журнал.
- Файлы: `src/pricewatch/application/stores_admin.py` — магазины у администратора: обзор (ссылки, следящие, состояние), название и цвет, включение и отключение, удаление неиспользуемого; журнал.
- Файлы: `src/pricewatch/application/users_admin.py` — пользователи у администратора: блокировка с отзывом сессий и устройств, разблокировка с возвратом удалённого блокировкой, снятие прав администратора, защита себя и последнего администратора, удаление неиспользованных приглашений; журнал.
- Файлы: `src/pricewatch/application/profile.py` — профиль: устройства и их отзыв, смена пароля (лимит как у входа, правила регистрации, остальные сессии и все устройства выходят); журнал.
- Файлы: `src/pricewatch/application/reviews.py` — «Требует решения»: карточки пользователя по его товарам, принять/отклонить с отменой, перепроверить сейчас (лимит, без дублей, отмена до выдачи), журнал.
- Файлы: `src/pricewatch/application/notifications.py` — колокольчик администратора: тексты уведомлений, ссылка на раздел, повторы, отметка прочитанного; уведомление о неожиданной ошибке сервера.
- Файлы: `src/pricewatch/application/services.py` — служебные процессы /internal/v1 (бот): токен как у worker (один раз, HMAC, ротация через следующий), пульс на каждый запрос.
- Файлы: `src/pricewatch/application/telegram.py` — привязка Telegram одноразовым кодом с сайта (15 минут; чат у одного пользователя), отвязка, код для приложения привязанному; сообщения для бота: захват на 2 минуты, отправлено, сбой — повтор через 2 минуты, до 30 попыток; журнал.
- Файлы: `src/pricewatch/application/summaries.py` — сводка проверки: по завершённому плановому или ручному запуску каждому с привязанным Telegram — «изменения есть / нет» по ценам, засчитанным ему; сообщения и отметка запуска в одной транзакции.
- Файлы: `src/pricewatch/application/health.py` — проверка здоровья: запрос к базе в транзакции; недоступна — DB_ERROR.
- Файлы: `src/pricewatch/application/watchdog.py` — сторож: worker или бот молчат дольше 5 минут — уведомление администраторам (бот — только колокольчик), снова на связи — сообщение о восстановлении; одно на инцидент.
- Файлы: `src/pricewatch/application/client_errors.py` — приём отчёта приложения: время, лимит на устройство, очистка текста, уведомление администраторам со склейкой по версии и коду; очистка старше 90 суток.
- Файлы: `src/pricewatch/application/price_view.py` — общая часть страниц: товары «в списке» с ценой сейчас и в начале периода, строка товара (цена по карте и без карты, за количество, статус, «на проверке»); срок актуальности цены — из настройки stale_after_days, в каждом товаре расчёта; история ссылки — как её видит пользователь (seen_by_user: его решения, эталон названия, с какого момента следит; общая со сводкой проверки); пороги подозрительных изменений из настроек — общие для приёма результата и расчёта по пользователю.
- Файлы: `src/pricewatch/application/category.py` — категория (блоки групп с суммами, итог, изменение), график категории по дням, группа (цены всех магазинов, лучшая, минимум периода с датой и магазином), обзор категорий для настроек (число товаров, итог, группы), обзор товаров для настроек (фильтр по категории, магазины, соседи по порядку).
- Файлы: `src/pricewatch/application/shopping.py` — категории и группы: создать, переименовать по версии, удалить с выбором (перенести товары или удалить вместе), токен «Отменить» на 15 с, порядок полным списком с версией родителя; товары: создать, изменить (в том числе категорию), удалить с отменой, порядок; ссылки по одной (ошибка одной не мешает остальным), снять с отменой, состояние ссылки по ТЗ; данные окна товара.
- Файлы: `src/pricewatch/application/purchases.py` — «Куплено» (форма с лучшей ценой, магазин из ссылок товара, день покупки — 00:00 по Москве, цена в момент добавления запоминается), «В архив», «Вернуть в список», вид покупки для страниц; страница «Покупки» за период (потрачено, экономия со знаком, разбивка по категориям, список) и архив с последней известной ценой.
- Файлы: `src/pricewatch/application/item.py` — карточка товара (цены магазинов, причина сбоя, фото, история за год), график по магазину, изменение количества и отметки «в итоге» с версией; отметка у неактуальной цены включает «устаревшую цену в итоге»; у купленного — покупка и «сейчас дешевле на …», у архивного — дата архива.
- Файлы: `src/pricewatch/application/idempotency.py` — ключ повтора: занять до выполнения, повтор с тем же телом — сохранённый ответ, с другим — 409, ошибка освобождает, оборванный — через 2 мин, срок 24 ч.
- Файлы: `src/pricewatch/application/dashboard.py` — главная: итог и изменение «только из-за цен», карточки категорий (график суммы за 30 дней, самый изменившийся товар), сводные панели и их списки по величине изменения, статус последней проверки (и отдельно для шапки — run_status), число изменений на проверке для администратора; статус проверки: идущая проверка без обращений worker дольше аренды (2 мин) — worker_offline, длительность.
- Файлы: `src/pricewatch/application/retention.py` — данные пользователя, заблокированного 7 суток назад: товары, группы, категории мягко удаляются; удалённое 30 дней назад стирается окончательно вместе с содержимым и привязками ссылок; общий каталог и история цен не трогаются.
- Файлы: `src/pricewatch/application/links.py` — добавление ссылки к товару: магазин создаётся сам, каталог без дублей, замена ссылки того же магазина, свежая цена (моложе суток) или срочное задание, лимит 20 новых ссылок в час.
- Файлы: `src/pricewatch/application/passwords.py` — PasswordHasher: argon2id (argon2-cffi), нормализация, фиктивная проверка для выравнивания времени.
- Файлы: `src/pricewatch/application/app_auth.py` — вход приложения: код связки (лимит по IP), устройство, ротация refresh (вывод через HMAC, окно 60 с, повтор позже — отзыв устройства), access-токен, выход.
- Файлы: `src/pricewatch/application/web_auth.py` — вход на сайт: лимиты до проверки пароля, неотличимые отказы, сессия, выход, CSRF-токен как HMAC сессии.
- Файлы: `src/pricewatch/application/accounts.py` — сценарии create_admin, create_invite (только активный администратор), register (по приглашению, неверные поля и занятый логин приглашение не тратят).
- Файлы: `src/pricewatch/application/backups.py` — копия на сервере: начало в журнал, проверка места (не хватает — NO_SPACE, ничего не удаляется), копия вне транзакции, только после удачной — удаление копий сверх двух (сбой — RETENTION_FAILED), итог в журнал. Сторож копий watch_backups: неудачная попытка — одно backup_failed на попытку, нет удачной копии больше 36 часов — одно backup_stale на эпизод, домашний приёмник не получал копий больше 36 часов — одно backup_not_pulled на эпизод; администраторам в колокольчик и Telegram, без повторов, под pg_advisory_xact_lock.
- Файлы: `src/pricewatch/application/backup_readers.py` — домашний приёмник копий: токен как у worker (один раз, HMAC с назначением BACKUP_READER, ротация через следующий), неверный токен — неудача по IP (10 за 15 минут — RATE_LIMITED), подтверждение получения копии.

### pricewatch.persistence
- Путь: `src/pricewatch/persistence/`
- Отвечает: модели БД, репозитории, миграции
- Зависит от: `pricewatch.application`, `pricewatch.config`, `pricewatch.domain`, `pricewatch.observability`
- Тесты: `uv run pytest tests/integration/test_database_connection.py tests/integration/test_migrations.py tests/integration/test_schema_constraints.py tests/integration/test_shopping_repositories.py tests/integration/test_concurrency.py tests/integration/test_schema_check.py tests/integration/test_user_repository.py tests/integration/test_database_failures.py tests/integration/test_queue_schema.py tests/integration/test_collection_repositories.py tests/integration/test_runs.py tests/integration/test_store_pauses.py tests/integration/test_retention.py tests/integration/test_dashboard.py tests/integration/test_idempotency.py tests/integration/test_telegram.py tests/integration/test_watchdog.py tests/integration/test_run_summaries.py`
- Файлы: `src/pricewatch/persistence/database.py` — подключение: малый пул, hide_parameters, соединение в UTC, DB_ERROR при недоступной базе.
- Файлы: `src/pricewatch/persistence/models.py` — таблицы списка покупок (составные ключи владельца), входа (в *_hash только HMAC в hex — проверка в базе) и сбора цен с очередью: магазины, ссылки, привязки, наблюдения, запуски, задания, worker, настройки, уведомления; гарантии раздела 8 — частичными уникальными индексами; имена ограничений по правилу.
- Файлы: `src/pricewatch/persistence/migrations/`, `src/pricewatch/persistence/migrations/versions/` — миграции Alembic; env.py берёт адрес базы из настроек (config) и пишет журнал в JSON (observability).
- Файлы: `src/pricewatch/persistence/idempotency_repository.py` — ключи повтора: вставка до выполнения (ON CONFLICT ждёт первую), ответ, освобождение, очистка старых.
- Файлы: `src/pricewatch/persistence/price_data_repository.py` — чтение для страниц: товары в неудалённых категориях и группах, все привязки ссылок, магазины, наблюдения с последнего успешного до начала окна (без чтения всей истории), адрес и фото ссылок, состояния действующих ссылок товара (магазин, проверяется ли, последняя проверка), число изменений на проверке; купленные товары с ценой в момент добавления (видимость как у товаров страниц); наблюдения с номером и названием; решения пользователя и его эталон названия по ссылкам.
- Файлы: `src/pricewatch/persistence/collection_repositories.py` — магазины, ссылки каталога, привязки, наблюдения, очередь (выдача FOR UPDATE SKIP LOCKED с блокировкой магазина, продление, закрытие со счётчиками; завершённые запуски без сводки — SKIP LOCKED, отметка сводки; запуск завершается, когда активных заданий не осталось; задания запуска одной вставкой; остановка и истёкшая аренда — блокировки в порядке «задание, потом запуск»; исчерпанная пауза закрывает ждущие задания магазина, запуски блокируются по возрастанию номера), пауза магазина, настройки, уведомления администраторам (повтор по dedup_key не создаёт строк), worker и сервисы (services — тот же репозиторий с другой таблицей): вставка ON CONFLICT по уникальным индексам; отметка молчания и восстановления одним UPDATE (два сторожа не сообщают дважды); условие частичного индекса — буквально (общий план подготовленного запроса); привязка: снять, вернуть по отмене (не вытесняя новую ссылку того же магазина); когда действующий worker последний раз обращался; настройки с версией: чтение и запись по версии; снять ждущее задание; ссылка наблюдения; обзор магазинов для администратора, изменение, включение, удаление неиспользуемого; уведомления: повтор поднимает счётчик и снова делает непрочитанным, колокольчик, отметка прочитанного.
- Файлы: `src/pricewatch/persistence/telegram_repository.py` — коды привязки Telegram (гашение одним UPDATE), чат пользователя, привязанные пользователи, захват Telegram-сообщений FOR UPDATE SKIP LOCKED с claimed_until, отметки отправки и сбоя.
- Файлы: `src/pricewatch/persistence/review_repositories.py` — решения пользователей (одно на наблюдение, отмена — удаление) и журнал audit_events; отчёты об ошибках приложения (запись, недавние по устройству, очистка). Окна лимитов полуоткрытые.
- Файлы: `src/pricewatch/persistence/schema.py` — настройка Alembic из кода, без alembic.ini; ревизия кода и базы, SchemaMismatch с подсказкой.
- Файлы: `src/pricewatch/persistence/shopping_repositories.py` — репозитории категорий, групп, товаров; изменения состава списка блокируют родителя подъёмом его версии; данные заблокированных — мягкое удаление всего сразу, занятые администратором пользователи пропускаются; окончательная очистка: привязки → товары → группы → категории, восстановленное за это время не стирается; перенос товаров при удалении категории или группы — в «без группы», в конец, с блокировкой родителя; смена категории товара — оба родителя блокируются в одном порядке; переходы «куплен / в архиве / в списке» по версии (порядок — только товары «в списке»).
- Файлы: `src/pricewatch/persistence/unit_of_work.py` — SqlUnitOfWork: одно соединение и транзакция, репозитории (в том числе backup_readers); без commit — откат; недоступность, обрыв и взаимная блокировка → DB_ERROR (503).
- Файлы: `src/pricewatch/persistence/user_repository.py` — пользователи; занятый логин без учёта регистра — LOGIN_TAKEN.
- Файлы: `src/pricewatch/persistence/app_auth_repositories.py` — коды связки, устройства, access- и refresh-токены; claim и find_refresh блокируют строку.
- Файлы: `src/pricewatch/persistence/auth_repositories.py` — неудачные попытки (окно, самая ранняя) и сессии сайта.
- Файлы: `src/pricewatch/persistence/invite_repository.py` — приглашения; claim блокирует строку (FOR UPDATE) до конца транзакции; времена записи — из часов сценария, не базы.
- Файлы: `src/pricewatch/persistence/backup_repository.py` — журнал копий backup_runs: начало, итог (повторное завершение не переписывает), состояние для сторожа (последняя завершённая, последняя удачная, первая попытка), блокировка сторожа.
- Файлы: `src/pricewatch/persistence/migrations/versions/0015_backup_runs.py` — таблица backup_runs: статус running/ok/failed, файл и размер у удачной, код у неудачной.
- Файлы: `src/pricewatch/persistence/backup_reader_repository.py` — приёмники копий backup_readers: токен как у worker (подкласс SqlWorkerRepository), отметка получения, сводка для сторожа.
- Файлы: `src/pricewatch/persistence/migrations/versions/0016_backup_readers.py` — таблица backup_readers (HMAC токена, следующий токен, last_pulled_at) и вид неудачи auth_failures.kind = 'backup'.

### pricewatch.api
- Путь: `src/pricewatch/api/`
- Отвечает: маршруты `/api/v1`, `/web`, `/worker/v1`, `/internal/v1`; аутентификация
- Зависит от: `pricewatch.application`, `pricewatch.config`, `pricewatch.domain`, `pricewatch.media`, `pricewatch.observability`, `pricewatch.web`
- Тесты: `uv run pytest tests/unit/test_api_app.py tests/security/test_client_ip.py tests/security/test_web_session_http.py tests/security/test_app_auth_http.py tests/security/test_access_rules.py tests/integration/test_server_start.py tests/security/test_worker_http.py tests/security/test_web_pages.py tests/security/test_dashboard_http.py tests/security/test_web_home.py tests/security/test_categories_http.py tests/security/test_web_categories.py tests/security/test_items_http.py tests/security/test_web_items.py tests/security/test_web_settings.py tests/security/test_web_settings_items.py tests/security/test_web_purchases.py tests/security/test_purchases_http.py tests/security/test_runs_http.py tests/security/test_web_runs.py tests/security/test_web_settings_check.py tests/security/test_reviews_http.py tests/security/test_web_reviews.py tests/security/test_web_settings_stores.py tests/security/test_web_settings_users.py tests/security/test_notifications_http.py tests/security/test_client_errors_http.py tests/security/test_web_profile.py tests/security/test_internal_http.py tests/security/test_health_http.py`
- Файлы: `src/pricewatch/api/worker_v1.py` — /worker/v1/claim (с признаком need_image), /heartbeat, /result, /image (тело image/webp до 2 МБ, читается частями): только токен worker, строгие схемы (лишние поля и неверные типы — 422); вне контракта приложения.
- Файлы: `src/pricewatch/api/app.py` — create_app (статика сайта и фото /media из каталога картинок): обработчики ошибок, CORS только своему домену, без автодокументации.
- Файлы: `src/pricewatch/api/edge.py` — внешний слой: request_id, защитные заголовки, 500 без утечек, журнал запросов без строки запроса; необработанное исключение — ещё и уведомление администраторам (не вышло — запись в журнале, ответ как обычно).
- Файлы: `src/pricewatch/api/errors.py` — HTTP-статус каждого кода ошибки, тело по схеме ErrorResponse; на путях сайта (/ и /web) — HTML через web_errors; записи domain в деталях (актуальное состояние при конфликте) — словарями.
- Файлы: `src/pricewatch/api/context.py` — ApiContext (единицы работы, ключ, хешер, часы, каталог фото, распознавание ссылок) от bootstrap; IP посетителя из request.state.
- Файлы: `src/pricewatch/api/web_auth.py` — cookie pw_session (HttpOnly, Secure, SameSite=Lax), зависимость web_user: только cookie; для изменяющих — Origin и X-CSRF-Token.
- Файлы: `src/pricewatch/api/v1_dashboard.py` — GET /api/v1/dashboard (ETag, 304), /dashboard/chart и /dashboard/items (непрозрачный курсор) по OpenAPI.
- Файлы: `src/pricewatch/api/v1_catalog.py` и `src/pricewatch/api/v1_management.py` — нативные операции каталога, покупок, архива и настроек. Общие сценарии `src/pricewatch/application`, версии записей и ключ идемпотентности.
- Файлы: `src/pricewatch/api/web_home.py` — главная сайта: страница, смена периода тем же адресом (htmx заменяет цифры и карточки), фрагмент списка панели страницами по 20.
- Файлы: `src/pricewatch/api/v1_json.py` — общие части ответов /api/v1: изменение, точка графика, магазин, строка товара, время UTC; курсор страниц (смещение).
- Файлы: `src/pricewatch/api/v1_categories.py` — GET /api/v1/categories/{id} (ETag, 304), /chart?range= и PUT /categories/order, /groups/{id}/items/order (ключ повтора, конфликт с актуальным порядком) по OpenAPI.
- Файлы: `src/pricewatch/api/web_categories.py` — страницы категории (период тем же адресом, график фрагментом по диапазону) и группы.
- Файлы: `src/pricewatch/api/v1_items.py` — GET/PATCH /api/v1/items/{id} (ETag; PATCH с версией и Idempotency-Key, конфликт с актуальной карточкой в details.current) и /chart?store_id=&range=.
- Файлы: `src/pricewatch/api/v1_purchases.py` — GET /api/v1/purchases (сводка периода, по категориям, страница покупок) и /archive (страница архива) по OpenAPI.
- Файлы: `src/pricewatch/api/v1_runs.py` — GET /api/v1/runs/current, POST /runs и /runs/{id}/stop (администратор, Idempotency-Key, 202; второй запуск — 409 с прогрессом).
- Файлы: `src/pricewatch/api/v1_reviews.py` — GET /api/v1/reviews (свои карточки, страницами) и POST /reviews/{id}/decision (Idempotency-Key, решённое или чужое — 404).
- Файлы: `src/pricewatch/api/v1_notifications.py` — GET /api/v1/notifications и POST /notifications/read (номера или все, Idempotency-Key), только администратор.
- Файлы: `src/pricewatch/api/internal_v1.py` — /internal/v1 для бота: только токен сервиса, каждый запрос — пульс; привязка чата, код для приложения, взять сообщения, отправлено/сбой; строгие тела.
- Файлы: `src/pricewatch/api/backups_v1.py` — /backups/v1 для домашнего приёмника (вне контракта приложения): только токен приёмника; список готовых копий с размером и sha256, скачивание по точному имени, подтверждение получения (строгое тело); папка не задана (PRICEWATCH_BACKUPS_DIR) — 404.
- Файлы: `src/pricewatch/api/v1_client_errors.py` — POST /api/v1/client_errors (bearer, Idempotency-Key, строгое тело, 201 с error_id, 429 сверх лимита).
- Файлы: `src/pricewatch/api/idempotency.py` — заголовок Idempotency-Key для изменяющих запросов: хеш метода, пути и тела; повтор возвращает сохранённый ответ; код успешного ответа задаётся (202 у запуска), повтор получает тот же.
- Файлы: `src/pricewatch/api/web_items.py` — карточка товара, фрагмент графика по магазину, изменение количества и отметки (htmx, CSRF, версия): ответ — обновлённая страница товара или категории; конфликт — актуальные данные и сообщение; «Куплено» (окно), «В архив», «Вернуть в список» (у купленного — окно подтверждения) с «Отменить»; возврат из архива отвечает страницей архива.
- Файлы: `src/pricewatch/api/web_purchases.py` — страница «Покупки» (период без перезагрузки, по умолчанию месяц) и вкладка «Архив».
- Файлы: `src/pricewatch/api/web_runs.py` — статус проверки в шапке и блок запуска администратора на главной: запуск, остановка, опрос раз в 5 с, пока проверка идёт.
- Файлы: `src/pricewatch/api/web_reviews.py` — «Требует решения» на главной: принять, отклонить, перепроверить; ответ — главная целиком с «Отменить».
- Файлы: `src/pricewatch/api/web_notifications.py` — колокольчик в шапке у администратора (подгружается фрагментом) и «Отметить все прочитанными».
- Файлы: `src/pricewatch/api/web_settings.py` — настройки «Категории»: создание, переименование в строке, удаление с окном выбора для непустых и «Отменить», порядок (кнопки, перетаскивание, с главной), группы; ответ — обновлённая страница.
- Файлы: `src/pricewatch/api/web_settings_items.py` — настройки «Товары»: список с фильтром, окно товара (новый после сохранения остаётся в окне в режиме изменения), группы по категории, ссылки (добавить, снять с отменой, обновление при проверке), удаление с отменой, порядок.
- Файлы: `src/pricewatch/api/web_settings_check.py` — настройки «Проверка» (только администратор): расписание и срок актуальности, ошибки у полей с сохранением введённого.
- Файлы: `src/pricewatch/api/web_settings_stores.py` — настройки «Магазины» (только администратор): правка в строке, отключение и удаление после подтверждения.
- Файлы: `src/pricewatch/api/web_settings_users.py` — настройки «Пользователи» (только администратор): окна подтверждения, отказы понятным текстом, приглашение показывается один раз, удаление выбранных.
- Файлы: `src/pricewatch/api/web_profile.py` — настройки «Профиль»: код для приложения, привязка Telegram кодом (ссылка t.me при заданном имени бота) и отвязка, устройства с отзывом, смена пароля с ошибками у полей, выход.
- Файлы: `src/pricewatch/api/health.py` — GET /healthz без входа: 200 {"status": "ok"} или 503 DB_ERROR, Cache-Control: no-store.
- Файлы: `src/pricewatch/api/web_pages.py` — / → /web/; вход и регистрация обычной формой (Origin = адрес сайта, без JS), недействительное приглашение — объяснение без формы, код приглашения в строке запроса; выход — htmx с CSRF; после входа — проверка, что браузер сохранил сессию (иначе объяснение на странице входа).
- Файлы: `src/pricewatch/api/web_errors.py` — ошибки сайта: нет сессии — на вход (htmx — HX-Redirect), иначе страница или фрагмент #flash с data-error-code (HX-Reselect: hx-select вызвавшего элемента не прячет сообщение); Retry-After при превышении попыток.
- Файлы: `src/pricewatch/api/web_forms.py` — разбор application/x-www-form-urlencoded: до 16 КБ и 50 полей, повтор поля и чужой тип — VALIDATION_ERROR; без python-multipart.
- Файлы: `src/pricewatch/api/app_auth.py` — зависимость app_user: только Authorization: Bearer, cookie не читается.
- Файлы: `src/pricewatch/api/v1_auth.py` — /api/v1/auth/pair, /refresh, /logout, /me по OpenAPI; ответы с токенами — Cache-Control: no-store; время — UTC.
- Файлы: `src/pricewatch/api/client_ip.py` — IP посетителя: X-Forwarded-For справа налево и только от доверенных прокси.

### pricewatch.web
- Путь: `src/pricewatch/web/`
- Отвечает: шаблоны, HTML-фрагменты, CSS, JS
- Зависит от: `pricewatch.domain`
- Тесты: `uv run pytest tests/unit/test_design_tokens.py tests/unit/test_fonts.py tests/unit/test_web_templates.py tests/security/test_web_pages.py tests/unit/test_package_layout.py tests/unit/test_web_charts.py tests/security/test_web_home.py tests/security/test_web_categories.py tests/security/test_web_items.py tests/security/test_web_settings.py tests/security/test_web_settings_items.py tests/security/test_web_purchases.py tests/security/test_web_runs.py tests/security/test_web_settings_check.py tests/security/test_web_reviews.py tests/security/test_web_settings_stores.py tests/security/test_web_settings_users.py tests/security/test_web_profile.py tests/integration/test_ui_pages.py`
- Файлы: `src/pricewatch/web/templates.py` — Jinja2: автоэкранирование, неизвестная переменная — ошибка, адрес статики с меткой содержимого.
- Файлы: `src/pricewatch/web/templates/` — base (шапка с колокольчиком администратора, статусом проверки — фрагмент с опросом, пока проверка идёт — и ссылкой «Покупки», htmx без eval и встроенных стилей, CSRF в hx-headers), главная и фрагмент панели, категория, группа, товар, настройки категорий, товаров, профиля, магазинов, проверки и пользователей (вкладки, код для приложения, «Магазины», «Проверка» и «Пользователи» — только администратору, правка магазина в строке и окна подтверждения, окна удаления и товара, блок ссылок, выбор группы, переименование в строке), фрагменты графиков категории и товара, окна «Куплено» и возврата покупки, блок «Куплено» в категории, страницы «Покупки» и «Архив», блок «Проверка цен» администратора, карточки «Требует решения», общие макросы (изменение, переключатель периода, графики, отметки статуса цены, галочка «в итоге» и счётчик количества), вход, регистрация, недействительное приглашение, ошибка, фрагмент #flash; без встроенных скриптов и style= (CSP).
- Файлы: `src/pricewatch/web/charts.py` — графики SVG с сервера: монотонная кривая Fritsch–Carlson без ложных выбросов, разрыв на днях без данных, ровная цена посередине; большой график — сетка «круглыми» рублями, даты, заливка, точка «сегодня», полосы наведения с готовой подписью.
- Файлы: `src/pricewatch/web/formats.py` — подписи: дата «6 октября 2026», время по Москве, названия периодов, строка статуса проверки; длительность проверки, статусы идущей проверки.
- Файлы: `src/pricewatch/web/static/css/site.css` — компоненты страниц: сводка с переключателем периода, карточки категорий, графики, сводные панели с раскрытием по высоте содержимого, бейджи изменений, статус проверки; шапка телефона в одну строку (статус — точка с подсказкой, имя и выход — в меню); цели нажатия на телефоне не меньше 24 px.
- Файлы: `src/pricewatch/web/static/js/app.js` — поведение без встроенного кода: подложка переключателя периода (откат при ошибке), одна открытая панель, «Свернуть» и Esc, подсказка большого графика; перетаскивание (порядок — в скрытую форму, отправляет htmx; на телефоне после удержания), окна (открываются по data-open, закрываются после успеха только с data-close-on-success), сообщение «Отменить» на 3 с; «Копировать» для ссылки приглашения; открытая вкладка видна на узком экране; обратный отсчёт кода для приложения; полный текст статуса проверки по нажатию на телефоне.
- Файлы: `src/pricewatch/web/static/css/base.css` — каркас: фон, шапка, кнопки, поля, сообщения, карточка входа; только токены.
- Файлы: `src/pricewatch/web/static/vendor/sortable-1.15.7.min.js`, `src/pricewatch/web/static/vendor/sortable.LICENSE.txt` — SortableJS 1.15.7 из npm (целостность sha512 сверена), MIT.
- Файлы: `src/pricewatch/web/static/vendor/htmx-2.0.10.min.js`, `src/pricewatch/web/static/vendor/htmx.LICENSE.txt` — htmx 2.0.10 из npm (целостность sha512 сверена), 0BSD.
- Файлы: `src/pricewatch/web/static/favicon.svg` — значок сайта: плитка «₽».
- Файлы: `src/pricewatch/web/static/css/tokens.css` — токены дизайна; компоненты берут цвета, размеры и длительности только отсюда.
- Файлы: `src/pricewatch/web/static/css/fonts.css`, `src/pricewatch/web/static/fonts/` — шрифты со своего домена: кириллица, латиница, расширенная латиница (знак ₽).

### pricewatch.parsers
- Путь: `src/pricewatch/parsers/`
- Отвечает: registry, общий парсер, адаптеры магазинов, нормализаторы
- Зависит от: `pricewatch.domain`
- Тесты: `uv run pytest tests/unit/test_package_layout.py tests/unit/test_parsers_urls.py tests/unit/test_parsers_registry.py tests/unit/test_parsers_fetch.py tests/unit/test_parsers_prices.py tests/unit/test_parsers_generic.py tests/unit/test_parsers_browser.py tests/unit/test_parsers_egress.py tests/integration/test_browser_egress.py tests/unit/test_parsers_wildberries.py tests/unit/test_parsers_regard.py tests/unit/test_parsers_visual.py tests/integration/test_visual_in_chrome.py tests/unit/test_parsers_universal.py tests/unit/test_parsers_dns.py tests/unit/test_parsers_ozon.py tests/unit/test_parsers_yandex_market.py tests/unit/test_parsers_avito.py tests/unit/test_parsers_catalog.py tests/integration/test_browser_sandbox.py`
- Файлы: `src/pricewatch/parsers/urls.py` — только https по имени (IP в любой записи, логин, порт — INVALID_URL), punycode, без фрагмента и меток рекламы, параметры по порядку; имя магазина без www. и m.
- Файлы: `src/pricewatch/parsers/contract.py` — ParsedProduct (цены в копейках, только RUB, регион, источник цены, название сайта, заметки) и интерфейс адаптера StoreParser.
- Файлы: `src/pricewatch/parsers/fetch.py` — SafeFetcher на httpx2: cookies магазина только на его сайт и поддомены; одно разрешение имени на шаг, все адреса публичные (NAT64, IPv4 в IPv6, многоадресные учтены), соединение на проверенный адрес с SNI по имени, перенаправления вручную до 5, предел 5 МБ после распаковки и 20 с на всё, прокси окружения игнорируются.
- Файлы: `src/pricewatch/parsers/prices.py` — цена в копейки без float: строгий разбор структурированных значений; кандидаты из текста через price-parser только при явном знаке рубля.
- Файлы: `src/pricewatch/parsers/generic.py` — product_from_json_ld для отдельной записи schema.org; общий парсер: JSON-LD (@graph, AggregateOffer, priceSpecification, варианты ProductGroup; вложенные похожие товары не читаются) → Open Graph и meta → микроразметка, источники не смешиваются; не рубли — UNSUPPORTED_CURRENCY; без цены — PRICE_NOT_FOUND с кандидатами; коды ответа магазина → ошибки сбора цен.
- Файлы: `src/pricewatch/parsers/egress.py` — проверяющий прокси для Chrome: только CONNECT имя:443 с публичными адресами, соединение на проверенный адрес, без вскрытия шифрования.
- Файлы: `src/pricewatch/parsers/browser.py` — BrowserFetcher: patchright + установленный Chrome в своей песочнице (chromium_sandbox; без неё Playwright добавляет --no-sandbox) с окном за краем экрана, до двух недавно открытых профилей, сброс только профиля текущего магазина, ожидание результата и навигация в пределах таймаута, перехват JSON страницы с заголовками её запросов (без cookies), запросы JSON изнутри открытой страницы (open → fetch_json), скрипт-зонд на каждом опросе (probe_js), скачивание файлов запрещено, весь трафик через EgressProxy.
- Файлы: `src/pricewatch/parsers/stores/wildberries.py` — Wildberries: страница в браузере → заголовки собственного запроса сайта → город и dest от геосервиса WB (подтверждение региона) → карточка с dest Санкт-Петербурга; цена в копейках, наличие, фото с CDN.
- Файлы: `src/pricewatch/parsers/visual.py` — цена «глазами» (VISUAL_JS, подход из парсера владельца): крупный незачёркнутый ценник рядом с h1, без «в месяц», бонусов и блоков внизу; наличие по кнопкам; цена в копейках.
- Файлы: `src/pricewatch/parsers/universal.py` — общий парсер для любого сайта: быстрый запрос → браузер (ответ браузера окончательный), способ запоминается для сайта; данные сайта → цена «глазами», расхождение больше 3 % — в заметки.
- Файлы: `src/pricewatch/parsers/stores/dns.py` — DNS через браузер: город из настроек страницы (cityGuid, Санкт-Петербург — 566ca284-…); ждёт три ответа страницы: название и фото — /product/microdata/<id>/ (логика JSON-LD), цена — кнопка «Купить» /ajax-state/product-buy/ (price.current), наличие — можно в корзину (data-cart-available в /ajax-state/avails-container/, без notAvail): магазин, пункт выдачи или доставка; OutOfStock в microdata значит только «нет в магазинах города».
- Файлы: `tests/fixtures/stores/dns` — ответы microdata, product-buy и avails-container как есть от 13.10.2026 для трёх товаров (в магазине; только доставка и пункты выдачи; нет в наличии) и сокращённая страница с настройками города.
- Файлы: `src/pricewatch/parsers/stores/ozon.py` — Ozon через браузер: цены парами из виджета webPrice («С банками» — по карте, «С другими банками» — обычная), город из location.current, наличие по кнопке корзины; закончившийся товар — перенаправление на поиск с виджетом webOutOfStock (название и фото из виджета); ссылки с product_id ведут на карточку.
- Файлы: `tests/fixtures/stores/ozon` — карточка и страница закончившегося товара от 06.10.2026, сокращённые до og-метаданных, h1, виджетов цены или webOutOfStock и города.
- Файлы: `src/pricewatch/parsers/stores/yandex_market.py` — Яндекс Маркет: быстрый запрос, при капче — браузер; регион из данных страницы (id 2 — Санкт-Петербург); цены из priceDetails («без карты» — обычная, «Пэй» — по карте), остальное из JSON-LD; «нет в продаже» — isNotOnSale.
- Файлы: `tests/fixtures/stores/yandex_market` — карточки в продаже и снятой с продажи от 06.10.2026, сокращённые до JSON-LD, h1, региона и priceDetails.
- Файлы: `src/pricewatch/parsers/stores/avito.py` — Авито: только браузер с профилем, где проверка пройдена вручную; цена продавца из JSON-LD; снятое объявление — по надписи «снято с публикации», она важнее JSON-LD; защита — CAPTCHA.
- Файлы: `tests/fixtures/stores/avito` — действующее и снятое объявления от 06.10.2026, сокращённые до og, JSON-LD и h1; тексты продавцов заменены.
- Файлы: `src/pricewatch/parsers/catalog.py` — справочник магазинов (реализует LinkResolver): известный магазин — карточка по его правилам и данные магазина, другой сайт — общий парсер, магазин создаётся сам; паузы 2 с, 5 с, 60 с.
- Файлы: `src/pricewatch/parsers/stores/regard.py` — Регард без браузера: cookie city_id (Санкт-Петербург — 137), город подтверждается town.cookieTownId из __NEXT_DATA__, цена — общим парсером.
- Файлы: `tests/fixtures/stores/regard` — настоящая страница товара от 06.10.2026, сокращённая: JSON-LD целиком (с похожими товарами) и состояние города.
- Файлы: `tests/fixtures/stores/wildberries` — образцы ответов WB от 06.10.2026: карточка товара, геоданные Санкт-Петербурга (без IP и подписи).
- Файлы: `src/pricewatch/parsers/registry.py` — адаптер по имени сайта: поддомены относятся к магазину, похожие имена — нет; одно имя — один адаптер.

### pricewatch.worker
- Путь: `src/pricewatch/worker/`
- Отвечает: клиент очереди, запуск парсеров, загрузка картинок; с сервером только по HTTP /worker/v1, к базе не подключается
- Зависит от: `pricewatch.config`, `pricewatch.domain`, `pricewatch.media`, `pricewatch.observability`, `pricewatch.parsers`
- Тесты: `uv run pytest tests/unit/test_worker_runner.py tests/unit/test_worker_images.py tests/integration/test_worker_end_to_end.py tests/integration/test_worker_start.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/worker/client.py` — клиент протокола: claim, heartbeat, result (сетевой сбой результата повторяется до 5 раз); 401 — токен не принят, 404 — аренда потеряна; прокси окружения не используются.
- Файлы: `src/pricewatch/worker/images.py` — фото по адресу из карточки: SafeFetcher с пределом 2 МБ, ответ не 200 — отказ, перекодирование в WebP.
- Файлы: `src/pricewatch/worker/parsing.py` — адаптер магазина по ключу из задания, иначе общий парсер со способом, который сервер помнит у магазина; Chrome использует ограниченный кэш открытых профилей.
- Файлы: `src/pricewatch/worker/runner.py` — цикл взять → пауза магазина (у браузерных +0–4 с случайно) → проверить → фото, если сервер его просит → отправить; в журнале длительность, магазин и исход проверки; сбой фото не мешает отправке цены; аренда продлевается каждые 30 с; неожиданная ошибка — в журнал и INTERNAL_ERROR; остановка после текущей проверки.
- Файлы: `src/pricewatch/worker/__main__.py` — запуск python -m pricewatch.worker: настройки PRICEWATCH_WORKER_* и worker.env, журнал JSON, SIGINT/SIGTERM; выход 2 — конфигурация, 4 — токен не принят.

### pricewatch.scheduler
- Путь: `src/pricewatch/scheduler/`
- Отвечает: плановые запуски; сценарии получает от bootstrap
- Зависит от: `pricewatch.application`, `pricewatch.domain`
- Тесты: `uv run pytest tests/unit/test_scheduler_loop.py tests/integration/test_scheduler_start.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/scheduler/loop.py` — обход каждые 30 с: просроченные задания, сторож (молчащие worker и бот — в журнал), сторож копий (сбой и «нет удачной копии 36 часов» — в журнал), сводки завершённых проверок, данные заблокированных 7 суток назад, очистка удалённого 30 дней назад и ключей повтора старше суток, наступивший слот (после старта — с 00:00 текущих суток: один пропущенный запуск), пропуск при идущем запуске — в журнал; сбой обхода — в журнал, слот повторится; стирает отчёты об ошибках приложения старше 90 суток.

### pricewatch.backup
- Путь: `src/pricewatch/backup/`
- Отвечает: резервные копии на сервере (сценарий и журнал получает от bootstrap, к базе обращается только pg_dump) и домашний приёмник копий (только HTTPS к серверу)
- Зависит от: `pricewatch.application`, `pricewatch.domain`
- Тесты: `uv run pytest tests/unit/test_backup_archive.py tests/unit/test_backup_service.py tests/unit/test_backup_pull.py tests/integration/test_backups.py tests/integration/test_backup_watch.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/backup/archive.py` — папка копий на сервере: pg_dump (пароль только в окружении) → tar с картинками потоком в age (открытый ключ) → временное имя → файл суммы <копия>.sha256 → переименование; остатки оборванных попыток удаляются; размер копии, свободное место, удаление копии вместе с суммой; сбои — коды DATABASE_DUMP_FAILED, ENCRYPTION_FAILED, STORAGE_FAILED, MEDIA_UNAVAILABLE.
- Файлы: `src/pricewatch/backup/pull.py` — домашний приёмник: список копий разбирается строго (лишние поля, типы, имена, больше 64 КБ — отказ), скачивает только новые и не больше лимита, сверяет размер и sha256 (не совпало или обрыв — файл удаляется), пишет под временным именем, подтверждает получение, дома хранит 30 суток; ничего не распаковывает, копии на сервере не удаляет.
- Файлы: `src/pricewatch/backup/service.py` — ежедневно в заданное время по Москве; остановка прерывает ожидание; сбой попытки — в журнал, служба живёт дальше; --once — одна копия.

### pricewatch.bot
- Путь: `src/pricewatch/bot/`
- Отвечает: Telegram-бот и сторож; с сервером только по HTTP /internal/v1, к базе не подключается
- Зависит от: `pricewatch.config`, `pricewatch.domain`, `pricewatch.observability`
- Тесты: `uv run pytest tests/unit/test_bot_telegram.py tests/unit/test_bot_runner.py tests/integration/test_bot_end_to_end.py tests/integration/test_bot_start.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/bot/telegram.py` — свой клиент Telegram Bot API на httpx2: getUpdates (только текст личных чатов), sendMessage простым текстом до 4096 символов, setMyCommands; 401/404 — токен не принят, 409 — другой экземпляр, 400/403 — чат недоступен, остальное — повторить позже (retry_after).
- Файлы: `src/pricewatch/bot/api.py` — клиент /internal/v1 с токеном сервиса: привязка, код для приложения, взять сообщения, отправлено/сбой; 401 — токен не принят, 404 — не найдено, остальное — сервер недоступен.
- Файлы: `src/pricewatch/bot/commands.py` — /start <код> — привязка, /code — код для приложения группами по 4, остальное — подсказка с номером чата; сервер недоступен — понятный ответ.
- Файлы: `src/pricewatch/bot/runner.py` — обход: рассылка (отправлено → отметка, чат недоступен или сбой Telegram → «сбой»), длинный опрос 10 с, ответы на команды (обновление подтверждается и при неотправленном ответе); паузы при недоступности; неверный токен — остановка; 409 — этот экземпляр не ведущий; неожиданная ошибка — в журнал.
- Файлы: `src/pricewatch/bot/watch.py` — сторож бота: сервер не отвечает 3 минуты — предупреждение администраторам из PRICEWATCH_BOT_ADMIN_CHAT_IDS, ответил — восстановление; только экземпляр, получающий обновления; одно на инцидент, повтор при сбое Telegram, неверный chat id — в журнал.
- Файлы: `src/pricewatch/bot/__main__.py` — запуск python -m pricewatch.bot: настройки PRICEWATCH_BOT_* и bot.env, журнал JSON, SIGINT/SIGTERM; выход 2 — конфигурация, 4 — токен сервиса или бота не принят.

### pricewatch.media
- Путь: `src/pricewatch/media/`
- Отвечает: перекодирование недоверенных фото в WebP и каталог картинок сервера; общий для worker и сервера
- Зависит от: `pricewatch.domain`
- Тесты: `uv run pytest tests/unit/test_media.py tests/security/test_worker_http.py tests/integration/test_worker_end_to_end.py`
- Файлы: `src/pricewatch/media/images.py` — to_webp: JPEG, PNG, WebP до 2 МБ и 40 Мп (размер по заголовку до распаковки), первый кадр, без метаданных, WebP не больше 800×800 (Pillow).
- Файлы: `src/pricewatch/media/store.py` — MediaStore (реализует ImageStore): присланный WebP перекодируется ещё раз, имя — SHA-256 содержимого «ab/<хеш>.webp», запись через временный файл.

### pricewatch.observability
- Путь: `src/pricewatch/observability/`
- Отвечает: логи, идентификаторы запросов и ошибок
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_observability_logging.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/observability/json_log.py` — JsonFormatter и configure_logging: одна JSON-строка на событие в stdout; use_utf8_streams — ввод и вывод процесса в UTF-8 (вызывают точки входа: bootstrap, миграции, worker).
- Файлы: `src/pricewatch/observability/redaction.py` — вырезание секретов из полей и текста, в том числе из traceback.
- Файлы: `src/pricewatch/observability/ids.py` — request_id в контексте выполнения, генератор error_id.

### pricewatch.bootstrap
- Путь: `src/pricewatch/bootstrap/`
- Отвечает: сборка приложения, связывает сценарии с репозиториями; без бизнес-логики
- Зависит от: `pricewatch.api`, `pricewatch.application`, `pricewatch.backup`, `pricewatch.config`, `pricewatch.domain`, `pricewatch.media`, `pricewatch.observability`, `pricewatch.parsers`, `pricewatch.persistence`, `pricewatch.scheduler`
- Тесты: `uv run pytest tests/integration/test_server_start.py tests/integration/test_create_admin_command.py tests/integration/test_worker_commands.py tests/integration/test_scheduler_start.py tests/integration/test_backup_start.py tests/unit/test_config_settings.py`
- Файлы: `src/pricewatch/bootstrap/scheduling.py` — процесс планировщика: настройки, журнал, проверка базы, сценарии запусков на SqlUnitOfWork, сигналы остановки; --backups — служба ежедневной копии в том же процессе (отдельным потоком, останавливается вместе с планировщиком); выход 2 — конфигурация, 3 — база.
- Файлы: `src/pricewatch/bootstrap/commands.py` — команды serve, scheduler, create-admin (пароль только скрытым вводом или из stdin), create-worker и rotate-worker-token, create-service и rotate-service-token, create-backup-reader и rotate-backup-reader-token (токен печатается один раз), pull-backups [--once].
- Файлы: `src/pricewatch/bootstrap/server.py`, `src/pricewatch/bootstrap/__main__.py` — настройки, журнал, одно подключение к базе на процесс, проверка схемы, контекст приложения (SqlUnitOfWork, ключ, argon2, часы UTC, каталог фото, каталог магазинов для ссылок), uvicorn; выход 2 — конфигурация, 3 — база или схема.
- Файлы: `src/pricewatch/bootstrap/backups.py` — команда backup [--once]: настройки сервера и backup.env, проверка базы; backup_service — служба копии для планировщика.
- Файлы: `src/pricewatch/bootstrap/pull.py` — команда pull-backups [--once]: pull.env, забор при старте и ежедневно в заданное время по Москве; сбой одного забора — в журнал.

### pricewatch.config
- Путь: `src/pricewatch/config/`
- Отвечает: настройки процесса из окружения и .env; ошибка без значений; секреты регистрируются для журнала; не импортируется domain и application
- Зависит от: `pricewatch.observability`
- Тесты: `uv run pytest tests/unit/test_config_settings.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/config/settings.py` — Settings (в том числе media_dir — каталог фото), load_settings, ConfigError; telegram_bot_username — имя бота для ссылки привязки (необязательно); BotSettings и load_bot_settings (PRICEWATCH_BOT_*, bot.env; токены сервиса и бота — секреты; chat id администраторов через запятую); api_origin — адрес своего сервера для worker и бота; WorkerSettings и load_worker_settings (PRICEWATCH_WORKER_*, worker.env; сервер и worker не видят переменных друг друга).

## Приложение iPhone

- Проект: `ios/PriceWatcher.xcodeproj`, схема PriceWatcher; настройки Docker и установки — `ios/README.md`.
- Файлы: `ios/PriceWatcher/App/PriceWatcherApp.swift` — подключение по pairing-коду, восстановление сессии и вкладки приложения.
- Файл: `ios/PriceWatcher/Features/ManagementView.swift` — нативные вкладки обзора, покупок и управления, роутинг форм и обновление экранов после записи.
- Файлы: `ios/PriceWatcher/Features/ProductEditor.swift`, `ios/PriceWatcher/Features/CategoryManager.swift`, `ios/PriceWatcher/Features/ActivityViews.swift` и `ios/PriceWatcher/Features/AccountViews.swift` — формы каталога, покупки, архив, уведомления и настройки SwiftUI.
- Файлы: `ios/PriceWatcher/Core/APIClient.swift`, `ios/PriceWatcher/Core/Models.swift`, `ios/PriceWatcher/Core/ServerAddress.swift`, `ios/PriceWatcher/Core/Keychain.swift` — JSON-контракт `openapi/v1.yaml`, сессия и адрес сервера.
- Файлы: `ios/PriceWatcher/Features/HomeView.swift`, `ios/PriceWatcher/Features/ProductView.swift`, `ios/PriceWatcher/Core/Design.swift` — итог, категории, товары, Swift Charts, редактирование количества и отметки, статус и управление запуском.
- Тесты: `ios/PriceWatcherTests/ContractTests.swift`, `ios/PriceWatcherUITests/ConnectionTests.swift`; команда `xcodebuild test -project ios/PriceWatcher.xcodeproj -scheme PriceWatcher -destination 'platform=iOS Simulator,name=iPhone 18 Pro'`.

## Границы модулей

Контракты в `pyproject.toml`, раздел `tool.importlinter`. Меняются только с согласия владельца.

- Тесты: `uv run pytest tests/unit/test_module_boundaries.py`
- Файлы: `tools/import_contracts.py` — свой тип контракта stdlib_only.

| id | Правило |
|---|---|
| `domain-stdlib-only` | `domain` импортирует только стандартную библиотеку и себя |
| `application-depends-on-domain-only` | `application` импортирует из проекта только `domain` |
| `persistence-implements-application` | `persistence` не импортирует `api`, `web`, `parsers`, `worker`, `scheduler`, `bot`, `backup`, `bootstrap` |
| `persistence-only-from-bootstrap` | `persistence` импортирует только `bootstrap` |
| `bootstrap-is-entry-point` | `bootstrap` никто не импортирует |
| `worker-bot-without-persistence` | `worker`, `bot` и `backup` не импортируют `persistence` |
| `store-adapters-independent` | модули `parsers.stores.*` (по одному на магазин) не импортируют друг друга |

## Инструменты

- Файлы: `tools/deps.py` — прямые зависимые модуля.
- Тесты: `uv run pytest tests/unit/test_tools_deps.py`
- Файлы: `tools/import_legacy.py` — одноразовый перенос данных старого сайта (SQLite) владельцу: категории, группы, товары, магазины, ссылки с окнами действия, запуски и проверки с ценой; одной транзакцией, повторный перенос отклоняется. Использует parsers.catalog и репозитории persistence (вне контрактов import-linter, как точка входа).
- Тесты: `uv run pytest tests/unit/test_tools_import_legacy.py tests/integration/test_import_legacy.py`
- Файлы: `tools/check_project_map.py` — проверка этой карты.
- Тесты: `uv run pytest tests/unit/test_check_project_map.py`

## Тесты

- Файлы: `tests/unit/` — unit-тесты.
- Файлы: `tests/integration/` — запуск настоящего процесса сервера, база, команды.
- Файлы: `tests/integration/concurrency_helpers.py` — пересечение транзакций с проверкой ожидания блокировки; импортируется тестами (pythonpath в pyproject).
- Файлы: `tests/integration/ui_site.py` — сайт в настоящем Chrome для проверок интерфейса: сервер в потоке на свободном порту, данные со всеми состояниями раздела 11 ТЗ, проверки раздела 12 в странице (подписи, контраст WCAG AA по слоям под текстом, страница не шире экрана, текст не вылезает и не заходит на поле карточки, многоточие только с полным текстом, точка магазина у названия, интерактивность только у кнопок и ссылок, закрытый заголовок; на телефоне цели от 24 px и шапка до 64 px), движение при reduced motion; импортируется тестами.
- Файлы: `tests/integration/test_ui_pages.py` — страницы администратора, пользователя, вход, регистрация и ошибки на 320 и 1440 в двух темах; ровные карточки категорий на 320–430, «Удалить» рядом с другими действиями, одна карточка «Требует решения» во всю ширину, видимый фокус с клавиатуры, диалоги с клавиатуры, reduced motion, самопроверка проверок; нужен установленный Google Chrome.
- Файлы: `tests/security/` — набор безопасности; обязателен при изменении входа, ролей, URL, worker (`AGENTS.md`, раздел 7).
- Команда: `uv run pytest tests/security` — только набор безопасности.
- Файлы: `tests/security/test_access_rules.py` — все маршруты /api/v1, кроме PUBLIC_API, требуют bearer; все действия из ADMIN_ONLY запрещены не-администраторам. Новое действие администратора добавляется в ADMIN_ONLY.
- Файлы: `tests/security/test_no_secrets_in_git.py` — нет .env, worker.env, bot.env, профилей браузера, ключей и токенов в файлах, которые попадут в коммит; они игнорируются, образцы .env.example, worker.env.example и bot.env.example без секретов.
- Файлы: `tests/contracts/` — проверки контракта API.
- Файлы: `tests/unit/conftest.py` — фикстура project_copy: копия проекта для тестов, которые ломают код.
- Файлы: `tests/conftest.py` — фикстура database_url: временная база PostgreSQL на прогон, удаляется в конце.

## Образец дизайна

Только чтение, в сборку не попадает. Читать только в задачах по интерфейсу.

- Файлы: `design_reference/NOTES.md` — пожелания владельца.
- Файлы: `design_reference/css/style.css` — старые стили; действующий набор токенов — фиолетовый, во второй половине файла.

Снимки по страницам:

- Файлы: `design_reference/screenshots/desktop/домашняя страница.png`, `design_reference/screenshots/desktop/домашняя страница 1.png` — главная, компьютер.
- Файлы: `design_reference/screenshots/mobile/главный экран.PNG`, `design_reference/screenshots/mobile/главный экран 1.PNG` — главная, телефон.
- Файлы: `design_reference/screenshots/desktop/категории.png`, `design_reference/screenshots/desktop/категории 1.png` — категория, компьютер.
- Файлы: `design_reference/screenshots/mobile/категория.PNG` — категория, телефон.
- Файлы: `design_reference/screenshots/desktop/карточка товара.png`, `design_reference/screenshots/desktop/карточка товара 1.png` — товар, компьютер.
- Файлы: `design_reference/screenshots/mobile/карточка товара.PNG`, `design_reference/screenshots/mobile/карточка товара 1.PNG`, `design_reference/screenshots/mobile/карточка товара2.PNG` — товар, телефон.
- Файлы: `design_reference/screenshots/desktop/Настройки товаров.png` — настройки товаров, компьютер.
- Файлы: `design_reference/screenshots/desktop/Требует решения.png`, `design_reference/screenshots/desktop/Требует решения 1.png` — блок «Требует решения» на главной (администратор), компьютер.
- Файлы: `design_reference/screenshots/desktop/добавление товара.png` — форма добавления товара, компьютер.
- Файлы: `design_reference/screenshots/mobile/Настройки категорий.PNG` — настройки категорий, телефон.

Без образца, по `SITE_DESIGN_SPEC.md`: группа, вкладки администратора, вход и регистрация.

## Данные и API

- Файлы: `openapi/v1.yaml` — контракт /api/v1 для приложения, утверждён. Меняется только с согласия владельца; совместимо — новыми необязательными полями.
- Тесты: `uv run pytest tests/contracts/test_openapi_document.py tests/contracts/test_routes_match_contract.py`

Схема базы: миграция 0001 — users, categories, groups, items; 0002 — invites, web_sessions, devices, refresh_tokens, access_tokens, pairing_codes, auth_failures; 0003 — stores, tracked_links, item_links, parse_runs, check_tasks, workers, price_observations, settings и items.state, items.purchased_at; 0004 — notifications; 0005 — items.include_stale; 0006 — idempotency_keys (уникально пользователь и ключ); 0007 — данные покупки и архива в items (цена, количество, магазин, цена в момент добавления, archived_at); 0008 — price_decisions (решение пользователя по подозрительному наблюдению); 0009 — audit_events (журнал действий); 0010 — notifications.repeat_count (повторы инцидента); 0011 — client_error_reports (отчёты об ошибках приложения); 0012 — services (токены и пульс служебных процессов), telegram_link_codes, notifications.claimed_until; 0013 — workers.silent_since и services.silent_since (сторож); 0014 — parse_runs; 0015 — backup_runs (журнал резервных копий); 0016 — backup_readers (токены домашних приёмников копий), auth_failures.kind 'backup'.summarized_at (сводка проверки создана; старые запуски отмечены при обновлении). Остальные таблицы раздела 5 добавляются миграциями своих этапов. Маршруты `/web`, `/worker/v1`, `/internal/v1`, `/backups/v1` в контракт приложения не входят.

## Сторонние материалы и готовые решения

Решения по разделу 8 `AGENTS.md`:

| Тема | Решение | Источник и лицензия | Отклонено |
|---|---|---|---|
| Хеширование паролей | argon2id через argon2-cffi | github.com/hynek/argon2-cffi, MIT | pwdlib (лишний слой), passlib (не работает на Python 3.13) |
| Ротация refresh-токенов, сессии | своя реализация по разделу 4 архитектуры; access-токен — случайная строка, в базе HMAC | RFC 9700 (OAuth 2.0 Security BCP), схема ротации с обнаружением повтора Auth0 — как образец | fastapi-users (режим поддержки, нет refresh), Authlib (сервер OAuth2 целиком), simplejwt (только Django, нет семей) |
| Ограничение попыток входа | своя таблица неудачных попыток в PostgreSQL | — | slowapi и limits (нет хранилища PostgreSQL), fastapi-limiter (нужен Redis) |
| Резервные копии | pg_dump (формат custom) + tar + age (открытый ключ на узле, закрытый — у владельца), отправка rsync по SSH в rrsync -wo -no-del -no-overwrite | github.com/FiloSottile/age, BSD-3 (пакет Debian); pg_dump — postgresql-client-18 из apt.postgresql.org, ключ B97B…4CF8 | restic (BSD-2), borg (BSD-3), kopia (Apache-2.0): ключ хранилища нужен на узле и для удаления старых копий — либо ключ на арендном сервере, либо дом может стирать копии; дедупликация при наших объёмах не нужна |
| Часовой пояс Москвы | `zoneinfo` стандартной библиотеки с данными пакета tzdata | github.com/python/tzdata, Apache-2.0 | фиксированное +3 (правила менялись в 2011 и 2014), системная база (может отсутствовать в образе) |
| HTTP-клиент парсера | httpx2 | github.com/pydantic/httpx2, BSD-3 | httpx (Starlette уже требует httpx2; два клиента не нужны) |
| Защита от SSRF | своя на httpx2: одно разрешение имени, проверка адреса, соединение на проверенный адрес, проверка каждого перенаправления | подход httpx-secure (github.com/Zaczero/pkgs, 0BSD) | httpx-secure (нужен второй клиент httpx, только async), ssrf-guard (новый, без истории), Advocate (в архиве) |
| Структурированные данные | свой разбор JSON-LD и meta/Open Graph на стандартной библиотеке | случаи `@graph` и AggregateOffer — по extruct (github.com/scrapinghub/extruct, BSD-3) и changedetection.io (Apache-2.0) | extruct (lxml, rdflib и ещё 5 пакетов, выпуск 11.2024) |
| Цена из текста | price-parser → Decimal → копейки с проверкой точности | github.com/scrapinghub/price-parser, BSD-3 | свой разбор (меньше форматов) |
| Браузер за защитой от ботов | patchright + установленный Chrome с окном, профиль на магазин, сброс профиля при метке робота, перехват JSON страницы | способ — из рабочего парсера владельца (prise-bot), переписан; github.com/Kaliiiiiiiiii-Vinyzu/patchright-python, Apache-2.0 | Playwright и Chrome без patchright (06.10.2026 не проходят Wildberries, Ozon, DNS), undetected-chromedriver (Selenium, ломается с новыми Chrome) |
| SSRF в браузере | свой проверяющий CONNECT-прокси (EgressProxy) | — | перехват `route` Playwright (не видит шаги перенаправлений, выключает кэш) |
| Wildberries | JSON `/__internal/u-card/cards/v4/detail`, который запрашивает сама страница в браузере | способ — из парсера владельца; адрес card.wb.ru закрыт защитой wbaas (06.10.2026) | открытый card.wb.ru (403 без токена), github.com/Duff89/wildberries_parser (устаревший адрес, без лицензии) |
| Очередь заданий | своя в PostgreSQL: check_tasks, FOR UPDATE SKIP LOCKED, аренда 2 мин, частичные уникальные индексы; worker — только через HTTP /worker/v1 | подход к периодическим задачам (уникальный ключ слота) — procrastinate (github.com/procrastinate-org/procrastinate, MIT) | procrastinate, pgqueuer (worker подключается к базе — ломает изоляцию worker), pgmq (расширение PostgreSQL) |
| Расписание | свой цикл: несколько времён в сутки по Москве, slot_key уникален в базе | — | APScheduler (лишняя сложность хранилища задач) |
| Шаблоны сайта | Jinja2 с автоэкранированием и строгими переменными (решение 06.10.2026) | github.com/pallets/jinja, BSD-3 | — |
| Обновление части страницы | htmx 2.0.10, файл в своей статике, без eval и встроенных стилей | github.com/bigskysoftware/htmx, 0BSD | свой JS на fetch (больше своего кода), htmx 4.0 (только вышел) |
| Перетаскивание | SortableJS + кнопки «выше/ниже» для клавиатуры (срез 7.4) | github.com/SortableJS/Sortable, MIT | встроенное перетаскивание браузера (не работает пальцем) |
| Графики | свой SVG с сервера по образцу design_reference, подсказка небольшим JS | — | uPlot, Chart.js (данные и деньги пришлось бы форматировать в JS) |
| Разбор форм | свой для application/x-www-form-urlencoded на urllib.parse | — | python-multipart (лишняя зависимость: файлов через формы нет) |
| CSRF | свой токен, привязанный к серверной сессии, и проверка Origin | — | starlette-csrf (не развивается с 2023), fastapi-csrf-protect (вторая cookie и секрет) |

Шрифты, лицензия SIL OFL 1.1, источник — npm-пакеты @fontsource-variable:
- Файлы: `src/pricewatch/web/static/fonts/unbounded.LICENSE.txt` — Unbounded 5.3.0, github.com/googlefonts/unbounded.
- Файлы: `src/pricewatch/web/static/fonts/onest.LICENSE.txt` — Onest 5.3.1, github.com/simpals/onest.
- Файлы: `src/pricewatch/web/static/fonts/jetbrains-mono.LICENSE.txt` — JetBrains Mono 5.3.0, github.com/JetBrains/JetBrainsMono.
