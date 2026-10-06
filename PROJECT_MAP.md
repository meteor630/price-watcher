# Карта проекта

Читается второй после `AGENTS.md`. Только короткие пункты и пути, без кода и секретов. Обновляется в той же задаче, что меняет модули, связи, данные, API, конфигурацию или команды тестов.

Проверка: `uv run python tools/check_project_map.py`. Формат, который она понимает, описан в начале `tools/check_project_map.py`. Пути пишутся в строках «Путь», «Файлы», «Тесты», «Команда». В строках «Путь» и «Файлы» в обратных кавычках — только пути.

## Команды

- Команда: `uv sync` — установить окружение.
- Команда: `uv run python -m pricewatch.bootstrap` — запустить сервер; настройки из окружения и .env; без базы или со старой схемой не стартует.
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

- Файлы: `pyproject.toml` — проект, зависимости, pytest, контракты import-linter.
- Файлы: `uv.lock` — точные версии.
- Файлы: `alembic.ini` — расположение миграций; адреса базы в нём нет.
- Файлы: `.python-version` — Python 3.13.
- Файлы: `.gitignore`
- Файлы: `.env.example` — все настройки с пояснениями, без секретов; копируется в .env. PRICEWATCH_SECRET_KEY — ключ HMAC для кодов, токенов, сессий; его смена разлогинивает всех.
- Файлы: `.github/workflows/ci.yml` — CI на каждый push: тесты, import-linter, проверка карты на Linux и на Windows (PostgreSQL 18 сборки EDB), gitleaks по всей истории. Действия закреплены по хешу, gitleaks и PostgreSQL для Windows — с проверкой контрольной суммы.
- Файлы: `.gitleaksignore` — ложные срабатывания gitleaks по точному отпечатку; только ненастоящие значения из тестов и только с согласия владельца.

Зависимости рабочего кода: pydantic-settings (MIT), fastapi (MIT), uvicorn (BSD-3), sqlalchemy (MIT), psycopg (LGPL-3.0, как неизменённая библиотека), alembic (MIT), argon2-cffi (MIT), tzdata (Apache-2.0), httpx2 (BSD-3), price-parser (BSD-3), patchright (Apache-2.0; нужен установленный Google Chrome, на Linux — Xvfb). Для разработки: pytest, import-linter, grimp, openapi-spec-validator.

## Пакеты

Направление зависимостей: `api → application → domain`; `persistence` реализует интерфейсы `application`; `bootstrap` собирает приложение. Правила — в разделе «Границы модулей».

### pricewatch.domain
- Путь: `src/pricewatch/domain/`
- Отвечает: сущности и правила расчётов; только стандартная библиотека
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_domain_errors.py tests/unit/test_domain_names.py tests/unit/test_domain_shopping.py tests/unit/test_domain_tokens.py tests/unit/test_domain_accounts.py tests/unit/test_domain_money.py tests/unit/test_domain_days.py tests/unit/test_domain_pricing.py tests/unit/test_domain_totals.py tests/unit/test_domain_charts.py tests/unit/test_domain_review.py tests/unit/test_domain_purchases.py`
- Файлы: `src/pricewatch/domain/errors.py` — коды ошибок, безопасные сообщения, базовое исключение PriceWatcherError.
- Файлы: `src/pricewatch/domain/names.py` — очистка имён для показа, ключ сравнения (регистр, пробелы, «ё», NFKC), пределы длины и количества.
- Файлы: `src/pricewatch/domain/shopping.py` — неизменяемые записи Category, Group, Item; состояния товара ItemState.
- Файлы: `src/pricewatch/domain/tokens.py` — случайные токены и коды связки, HMAC с назначением, вывод следующего токена из предыдущего.
- Файлы: `src/pricewatch/domain/accounts.py` — роли, User, правила логина и пароля (NFKC), отображаемое имя.
- Файлы: `src/pricewatch/domain/money.py` — целые копейки, строки «12 990 ₽», «−652 ₽», процент с одним знаком (половины вверх), изменение Change.
- Файлы: `src/pricewatch/domain/pricing.py` — наблюдение, действующая цена, участие ссылки (меньше 7 суток), статус ссылки и товара по порядку раздела 6, лучшая цена, «на проверке»; расчёт на любой момент (проверки отсортированы один раз, граница — двоичным поиском).
- Файлы: `src/pricewatch/domain/totals.py` — привязки ссылок [valid_from, valid_to), цена товара на момент, отметка «в итоге» при устаревшей цене, итог, изменение «только из-за цен», минимум периода.
- Файлы: `src/pricewatch/domain/charts.py` — точки по московским дням (7/30/365), график магазина (цена за штуку, продолжение при замене ссылки), график суммы по сегодняшнему составу.
- Файлы: `src/pricewatch/domain/review.py` — смена названия (схожесть ниже 85 %) и скачок цены (больше 30 %) точно, в целых числах; вердикт, автоматическое закрытие ценового подозрения, пороги.
- Файлы: `src/pricewatch/domain/purchases.py` — переходы «куплен / в архиве / в списке», проверка данных покупки, 14 суток проверки после покупки, «сейчас дешевле», цена в момент добавления, «потрачено» и экономия по периодам и категориям.
- Файлы: `src/pricewatch/domain/days.py` — сутки с 00:00 по Москве (zoneinfo + tzdata), начала периодов day/week/month/year (0/7/30/365 суток), подписи «5 окт».

### pricewatch.application
- Путь: `src/pricewatch/application/`
- Отвечает: сценарии и интерфейсы репозиториев
- Зависит от: `pricewatch.domain`
- Тесты: `uv run pytest tests/unit/test_passwords.py tests/integration/test_create_admin_command.py tests/integration/test_registration.py tests/integration/test_web_login.py tests/integration/test_app_auth.py`
- Файлы: `src/pricewatch/application/ports.py` — интерфейсы репозиториев и единицы работы; общие правила владельца, версий, перестановки, удаления.
- Файлы: `src/pricewatch/application/passwords.py` — PasswordHasher: argon2id (argon2-cffi), нормализация, фиктивная проверка для выравнивания времени.
- Файлы: `src/pricewatch/application/app_auth.py` — вход приложения: код связки (лимит по IP), устройство, ротация refresh (вывод через HMAC, окно 60 с, повтор позже — отзыв устройства), access-токен, выход.
- Файлы: `src/pricewatch/application/web_auth.py` — вход на сайт: лимиты до проверки пароля, неотличимые отказы, сессия, выход, CSRF-токен как HMAC сессии.
- Файлы: `src/pricewatch/application/accounts.py` — сценарии create_admin, create_invite (только активный администратор), register (по приглашению, неверные поля и занятый логин приглашение не тратят).

### pricewatch.persistence
- Путь: `src/pricewatch/persistence/`
- Отвечает: модели БД, репозитории, миграции
- Зависит от: `pricewatch.application`, `pricewatch.config`, `pricewatch.domain`, `pricewatch.observability`
- Тесты: `uv run pytest tests/integration/test_database_connection.py tests/integration/test_migrations.py tests/integration/test_schema_constraints.py tests/integration/test_shopping_repositories.py tests/integration/test_concurrency.py tests/integration/test_schema_check.py tests/integration/test_user_repository.py tests/integration/test_database_failures.py`
- Файлы: `src/pricewatch/persistence/database.py` — подключение: малый пул, hide_parameters, соединение в UTC, DB_ERROR при недоступной базе.
- Файлы: `src/pricewatch/persistence/models.py` — таблицы списка покупок (составные ключи владельца) и входа (в *_hash только HMAC в hex — проверка в базе); имена ограничений по правилу.
- Файлы: `src/pricewatch/persistence/migrations/`, `src/pricewatch/persistence/migrations/versions/` — миграции Alembic; env.py берёт адрес базы из настроек (config) и пишет журнал в JSON (observability).
- Файлы: `src/pricewatch/persistence/schema.py` — настройка Alembic из кода, без alembic.ini; ревизия кода и базы, SchemaMismatch с подсказкой.
- Файлы: `src/pricewatch/persistence/shopping_repositories.py` — репозитории категорий, групп, товаров; изменения состава списка блокируют родителя подъёмом его версии.
- Файлы: `src/pricewatch/persistence/unit_of_work.py` — SqlUnitOfWork: одно соединение и транзакция; без commit — откат; недоступность, обрыв и взаимная блокировка → DB_ERROR (503).
- Файлы: `src/pricewatch/persistence/user_repository.py` — пользователи; занятый логин без учёта регистра — LOGIN_TAKEN.
- Файлы: `src/pricewatch/persistence/app_auth_repositories.py` — коды связки, устройства, access- и refresh-токены; claim и find_refresh блокируют строку.
- Файлы: `src/pricewatch/persistence/auth_repositories.py` — неудачные попытки (окно, самая ранняя) и сессии сайта.
- Файлы: `src/pricewatch/persistence/invite_repository.py` — приглашения; claim блокирует строку (FOR UPDATE) до конца транзакции; времена записи — из часов сценария, не базы.

### pricewatch.api
- Путь: `src/pricewatch/api/`
- Отвечает: маршруты `/api/v1`, `/web`, `/worker/v1`, `/internal/v1`; аутентификация
- Зависит от: `pricewatch.application`, `pricewatch.config`, `pricewatch.domain`, `pricewatch.observability`
- Тесты: `uv run pytest tests/unit/test_api_app.py tests/security/test_client_ip.py tests/security/test_web_session_http.py tests/security/test_app_auth_http.py tests/security/test_access_rules.py tests/integration/test_server_start.py`
- Файлы: `src/pricewatch/api/app.py` — create_app: обработчики ошибок, CORS только своему домену, без автодокументации.
- Файлы: `src/pricewatch/api/edge.py` — внешний слой: request_id, защитные заголовки, 500 без утечек, журнал запросов без строки запроса.
- Файлы: `src/pricewatch/api/errors.py` — HTTP-статус каждого кода ошибки, тело по схеме ErrorResponse.
- Файлы: `src/pricewatch/api/context.py` — ApiContext (единицы работы, ключ, хешер, часы) от bootstrap; IP посетителя из request.state.
- Файлы: `src/pricewatch/api/web_auth.py` — cookie pw_session (HttpOnly, Secure, SameSite=Lax), зависимость web_user: только cookie; для изменяющих — Origin и X-CSRF-Token.
- Файлы: `src/pricewatch/api/app_auth.py` — зависимость app_user: только Authorization: Bearer, cookie не читается.
- Файлы: `src/pricewatch/api/v1_auth.py` — /api/v1/auth/pair, /refresh, /logout, /me по OpenAPI; ответы с токенами — Cache-Control: no-store; время — UTC.
- Файлы: `src/pricewatch/api/client_ip.py` — IP посетителя: X-Forwarded-For справа налево и только от доверенных прокси.

### pricewatch.web
- Путь: `src/pricewatch/web/`
- Отвечает: шаблоны, HTML-фрагменты, CSS, JS
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_design_tokens.py tests/unit/test_fonts.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/web/static/css/tokens.css` — токены дизайна; компоненты берут цвета, размеры и длительности только отсюда.
- Файлы: `src/pricewatch/web/static/css/fonts.css`, `src/pricewatch/web/static/fonts/` — шрифты со своего домена: кириллица, латиница, расширенная латиница (знак ₽).

### pricewatch.parsers
- Путь: `src/pricewatch/parsers/`
- Отвечает: registry, общий парсер, адаптеры магазинов, нормализаторы
- Зависит от: `pricewatch.domain`
- Тесты: `uv run pytest tests/unit/test_package_layout.py tests/unit/test_parsers_urls.py tests/unit/test_parsers_registry.py tests/unit/test_parsers_fetch.py tests/unit/test_parsers_prices.py tests/unit/test_parsers_generic.py tests/unit/test_parsers_egress.py tests/integration/test_browser_egress.py tests/unit/test_parsers_wildberries.py tests/unit/test_parsers_regard.py tests/unit/test_parsers_visual.py tests/integration/test_visual_in_chrome.py tests/unit/test_parsers_universal.py tests/unit/test_parsers_dns.py tests/unit/test_parsers_ozon.py`
- Файлы: `src/pricewatch/parsers/urls.py` — только https по имени (IP в любой записи, логин, порт — INVALID_URL), punycode, без фрагмента и меток рекламы, параметры по порядку; имя магазина без www. и m.
- Файлы: `src/pricewatch/parsers/contract.py` — ParsedProduct (цены в копейках, только RUB, регион, источник цены, название сайта, заметки) и интерфейс адаптера StoreParser.
- Файлы: `src/pricewatch/parsers/fetch.py` — SafeFetcher на httpx2: cookies магазина только на его сайт и поддомены; одно разрешение имени на шаг, все адреса публичные (NAT64, IPv4 в IPv6, многоадресные учтены), соединение на проверенный адрес с SNI по имени, перенаправления вручную до 5, предел 5 МБ после распаковки и 20 с на всё, прокси окружения игнорируются.
- Файлы: `src/pricewatch/parsers/prices.py` — цена в копейки без float: строгий разбор структурированных значений; кандидаты из текста через price-parser только при явном знаке рубля.
- Файлы: `src/pricewatch/parsers/generic.py` — product_from_json_ld для отдельной записи schema.org; общий парсер: JSON-LD (@graph, AggregateOffer, priceSpecification, варианты ProductGroup; вложенные похожие товары не читаются) → Open Graph и meta → микроразметка, источники не смешиваются; не рубли — UNSUPPORTED_CURRENCY; без цены — PRICE_NOT_FOUND с кандидатами; коды ответа магазина → ошибки сбора цен.
- Файлы: `src/pricewatch/parsers/egress.py` — проверяющий прокси для Chrome: только CONNECT имя:443 с публичными адресами, соединение на проверенный адрес, без вскрытия шифрования.
- Файлы: `src/pricewatch/parsers/browser.py` — BrowserFetcher: patchright + установленный Chrome с окном за краем экрана, профиль на магазин со сбросом при метке робота, ожидание результата, перехват JSON страницы с заголовками её запросов (без cookies), запросы JSON изнутри открытой страницы (open → fetch_json), скрипт-зонд на каждом опросе (probe_js), скачивание файлов запрещено, весь трафик через EgressProxy.
- Файлы: `src/pricewatch/parsers/stores/wildberries.py` — Wildberries: страница в браузере → заголовки собственного запроса сайта → город и dest от геосервиса WB (подтверждение региона) → карточка с dest Санкт-Петербурга; цена в копейках, наличие, фото с CDN.
- Файлы: `src/pricewatch/parsers/visual.py` — цена «глазами» (VISUAL_JS, подход из парсера владельца): крупный незачёркнутый ценник рядом с h1, без «в месяц», бонусов и блоков внизу; наличие по кнопкам; цена в копейках.
- Файлы: `src/pricewatch/parsers/universal.py` — общий парсер для любого сайта: быстрый запрос → браузер (ответ браузера окончательный), способ запоминается для сайта; данные сайта → цена «глазами», расхождение больше 3 % — в заметки.
- Файлы: `src/pricewatch/parsers/stores/dns.py` — DNS через браузер: город из настроек страницы (cityGuid, Санкт-Петербург — 566ca284-…), цена из JSON /product/microdata/<id>/ той же логикой, что JSON-LD.
- Файлы: `tests/fixtures/stores/dns` — JSON microdata и сокращённая страница с настройками города от 06.10.2026.
- Файлы: `src/pricewatch/parsers/stores/ozon.py` — Ozon через браузер: цены парами из виджета webPrice («С банками» — по карте, «С другими банками» — обычная), город из location.current, наличие по кнопке корзины; закончившийся товар — перенаправление на поиск с виджетом webOutOfStock (название и фото из виджета); ссылки с product_id ведут на карточку.
- Файлы: `tests/fixtures/stores/ozon` — карточка и страница закончившегося товара от 06.10.2026, сокращённые до og-метаданных, h1, виджетов цены или webOutOfStock и города.
- Файлы: `src/pricewatch/parsers/stores/regard.py` — Регард без браузера: cookie city_id (Санкт-Петербург — 137), город подтверждается town.cookieTownId из __NEXT_DATA__, цена — общим парсером.
- Файлы: `tests/fixtures/stores/regard` — настоящая страница товара от 06.10.2026, сокращённая: JSON-LD целиком (с похожими товарами) и состояние города.
- Файлы: `tests/fixtures/stores/wildberries` — образцы ответов WB от 06.10.2026: карточка товара, геоданные Санкт-Петербурга (без IP и подписи).
- Файлы: `src/pricewatch/parsers/registry.py` — адаптер по имени сайта: поддомены относятся к магазину, похожие имена — нет; одно имя — один адаптер.

### pricewatch.worker
- Путь: `src/pricewatch/worker/`
- Отвечает: клиент очереди, запуск парсеров, загрузка картинок
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_package_layout.py`

### pricewatch.scheduler
- Путь: `src/pricewatch/scheduler/`
- Отвечает: плановые запуски
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_package_layout.py`

### pricewatch.bot
- Путь: `src/pricewatch/bot/`
- Отвечает: Telegram-бот и сторож
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_package_layout.py`

### pricewatch.observability
- Путь: `src/pricewatch/observability/`
- Отвечает: логи, идентификаторы запросов и ошибок
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_observability_logging.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/observability/json_log.py` — JsonFormatter и configure_logging: одна JSON-строка на событие в stdout; use_utf8_streams — ввод и вывод процесса в UTF-8 (вызывают точки входа: bootstrap, миграции).
- Файлы: `src/pricewatch/observability/redaction.py` — вырезание секретов из полей и текста, в том числе из traceback.
- Файлы: `src/pricewatch/observability/ids.py` — request_id в контексте выполнения, генератор error_id.

### pricewatch.bootstrap
- Путь: `src/pricewatch/bootstrap/`
- Отвечает: сборка приложения, связывает сценарии с репозиториями; без бизнес-логики
- Зависит от: `pricewatch.api`, `pricewatch.application`, `pricewatch.config`, `pricewatch.domain`, `pricewatch.observability`, `pricewatch.persistence`
- Тесты: `uv run pytest tests/integration/test_server_start.py tests/integration/test_create_admin_command.py`
- Файлы: `src/pricewatch/bootstrap/commands.py` — команды serve и create-admin; пароль только скрытым вводом или из stdin.
- Файлы: `src/pricewatch/bootstrap/server.py`, `src/pricewatch/bootstrap/__main__.py` — настройки, журнал, одно подключение к базе на процесс, проверка схемы, контекст приложения (SqlUnitOfWork, ключ, argon2, часы UTC), uvicorn; выход 2 — конфигурация, 3 — база или схема.

### pricewatch.config
- Путь: `src/pricewatch/config/`
- Отвечает: настройки процесса из окружения и .env; ошибка без значений; секреты регистрируются для журнала; не импортируется domain и application
- Зависит от: `pricewatch.observability`
- Тесты: `uv run pytest tests/unit/test_config_settings.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/config/settings.py` — Settings, load_settings, ConfigError.

## Границы модулей

Контракты в `pyproject.toml`, раздел `tool.importlinter`. Меняются только с согласия владельца.

- Тесты: `uv run pytest tests/unit/test_module_boundaries.py`
- Файлы: `tools/import_contracts.py` — свой тип контракта stdlib_only.

| id | Правило |
|---|---|
| `domain-stdlib-only` | `domain` импортирует только стандартную библиотеку и себя |
| `application-depends-on-domain-only` | `application` импортирует из проекта только `domain` |
| `persistence-implements-application` | `persistence` не импортирует `api`, `web`, `parsers`, `worker`, `scheduler`, `bot`, `bootstrap` |
| `persistence-only-from-bootstrap` | `persistence` импортирует только `bootstrap` |
| `bootstrap-is-entry-point` | `bootstrap` никто не импортирует |
| `worker-bot-without-persistence` | `worker` и `bot` не импортируют `persistence` |
| `store-adapters-independent` | модули `parsers.stores.*` (по одному на магазин) не импортируют друг друга |

## Инструменты

- Файлы: `tools/deps.py` — прямые зависимые модуля.
- Тесты: `uv run pytest tests/unit/test_tools_deps.py`
- Файлы: `tools/check_project_map.py` — проверка этой карты.
- Тесты: `uv run pytest tests/unit/test_check_project_map.py`

## Тесты

- Файлы: `tests/unit/` — unit-тесты.
- Файлы: `tests/integration/` — запуск настоящего процесса сервера, база, команды.
- Файлы: `tests/integration/concurrency_helpers.py` — пересечение транзакций с проверкой ожидания блокировки; импортируется тестами (pythonpath в pyproject).
- Файлы: `tests/security/` — набор безопасности; обязателен при изменении входа, ролей, URL, worker (`AGENTS.md`, раздел 7).
- Команда: `uv run pytest tests/security` — только набор безопасности.
- Файлы: `tests/security/test_access_rules.py` — все маршруты /api/v1, кроме PUBLIC_API, требуют bearer; все действия из ADMIN_ONLY запрещены не-администраторам. Новое действие администратора добавляется в ADMIN_ONLY.
- Файлы: `tests/security/test_no_secrets_in_git.py` — нет .env, ключей и токенов в файлах, которые попадут в коммит; .env игнорируется, .env.example без секретов.
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

Схема базы: миграция 0001 — users, categories, groups, items; 0002 — invites, web_sessions, devices, refresh_tokens, access_tokens, pairing_codes, auth_failures. Остальные таблицы раздела 5 добавляются миграциями своих этапов. Маршруты `/web`, `/worker/v1`, `/internal/v1` в контракт приложения не входят.

## Сторонние материалы и готовые решения

Решения по разделу 8 `AGENTS.md`:

| Тема | Решение | Источник и лицензия | Отклонено |
|---|---|---|---|
| Хеширование паролей | argon2id через argon2-cffi | github.com/hynek/argon2-cffi, MIT | pwdlib (лишний слой), passlib (не работает на Python 3.13) |
| Ротация refresh-токенов, сессии | своя реализация по разделу 4 архитектуры; access-токен — случайная строка, в базе HMAC | RFC 9700 (OAuth 2.0 Security BCP), схема ротации с обнаружением повтора Auth0 — как образец | fastapi-users (режим поддержки, нет refresh), Authlib (сервер OAuth2 целиком), simplejwt (только Django, нет семей) |
| Ограничение попыток входа | своя таблица неудачных попыток в PostgreSQL | — | slowapi и limits (нет хранилища PostgreSQL), fastapi-limiter (нужен Redis) |
| Часовой пояс Москвы | `zoneinfo` стандартной библиотеки с данными пакета tzdata | github.com/python/tzdata, Apache-2.0 | фиксированное +3 (правила менялись в 2011 и 2014), системная база (может отсутствовать в образе) |
| HTTP-клиент парсера | httpx2 | github.com/pydantic/httpx2, BSD-3 | httpx (Starlette уже требует httpx2; два клиента не нужны) |
| Защита от SSRF | своя на httpx2: одно разрешение имени, проверка адреса, соединение на проверенный адрес, проверка каждого перенаправления | подход httpx-secure (github.com/Zaczero/pkgs, 0BSD) | httpx-secure (нужен второй клиент httpx, только async), ssrf-guard (новый, без истории), Advocate (в архиве) |
| Структурированные данные | свой разбор JSON-LD и meta/Open Graph на стандартной библиотеке | случаи `@graph` и AggregateOffer — по extruct (github.com/scrapinghub/extruct, BSD-3) и changedetection.io (Apache-2.0) | extruct (lxml, rdflib и ещё 5 пакетов, выпуск 11.2024) |
| Цена из текста | price-parser → Decimal → копейки с проверкой точности | github.com/scrapinghub/price-parser, BSD-3 | свой разбор (меньше форматов) |
| Браузер за защитой от ботов | patchright + установленный Chrome с окном, профиль на магазин, сброс профиля при метке робота, перехват JSON страницы | способ — из рабочего парсера владельца (prise-bot), переписан; github.com/Kaliiiiiiiiii-Vinyzu/patchright-python, Apache-2.0 | Playwright и Chrome без patchright (06.10.2026 не проходят Wildberries, Ozon, DNS), undetected-chromedriver (Selenium, ломается с новыми Chrome) |
| SSRF в браузере | свой проверяющий CONNECT-прокси (EgressProxy) | — | перехват `route` Playwright (не видит шаги перенаправлений, выключает кэш) |
| Wildberries | JSON `/__internal/u-card/cards/v4/detail`, который запрашивает сама страница в браузере | способ — из парсера владельца; адрес card.wb.ru закрыт защитой wbaas (06.10.2026) | открытый card.wb.ru (403 без токена), github.com/Duff89/wildberries_parser (устаревший адрес, без лицензии) |
| CSRF | свой токен, привязанный к серверной сессии, и проверка Origin | — | starlette-csrf (не развивается с 2023), fastapi-csrf-protect (вторая cookie и секрет) |

Шрифты, лицензия SIL OFL 1.1, источник — npm-пакеты @fontsource-variable:
- Файлы: `src/pricewatch/web/static/fonts/unbounded.LICENSE.txt` — Unbounded 5.3.0, github.com/googlefonts/unbounded.
- Файлы: `src/pricewatch/web/static/fonts/onest.LICENSE.txt` — Onest 5.3.1, github.com/simpals/onest.
- Файлы: `src/pricewatch/web/static/fonts/jetbrains-mono.LICENSE.txt` — JetBrains Mono 5.3.0, github.com/JetBrains/JetBrainsMono.
