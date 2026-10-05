# Карта проекта

Читается второй после `AGENTS.md`. Только короткие пункты и пути, без кода и секретов. Обновляется в той же задаче, что меняет модули, связи, данные, API, конфигурацию или команды тестов.

Проверка: `uv run python tools/check_project_map.py`. Формат, который она понимает, описан в начале `tools/check_project_map.py`. Пути пишутся в строках «Путь», «Файлы», «Тесты», «Команда». В строках «Путь» и «Файлы» в обратных кавычках — только пути.

## Команды

- Команда: `uv sync` — установить окружение.
- Команда: `uv run python -m pricewatch.bootstrap` — запустить сервер; настройки из окружения и .env.
- Команда: `uv run alembic upgrade head` — применить миграции; адрес базы из настроек.
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
- Файлы: `.env.example` — все настройки с пояснениями, без секретов; копируется в .env.
- Файлы: `.github/workflows/ci.yml` — CI на каждый push: тесты, import-linter, проверка карты, gitleaks по всей истории. Действия закреплены по хешу, gitleaks — с проверкой контрольной суммы.
- Файлы: `.gitleaksignore` — ложные срабатывания gitleaks по точному отпечатку; только ненастоящие значения из тестов и только с согласия владельца.

Зависимости рабочего кода: pydantic-settings (MIT), fastapi (MIT), uvicorn (BSD-3), sqlalchemy (MIT), psycopg (LGPL-3.0, как неизменённая библиотека). Для разработки: pytest, import-linter, grimp, openapi-spec-validator, httpx2.

## Пакеты

Направление зависимостей: `api → application → domain`; `persistence` реализует интерфейсы `application`; `bootstrap` собирает приложение. Правила — в разделе «Границы модулей».

### pricewatch.domain
- Путь: `src/pricewatch/domain/`
- Отвечает: сущности и правила расчётов; только стандартная библиотека
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_domain_errors.py tests/unit/test_package_layout.py`
- Файлы: `src/pricewatch/domain/errors.py` — коды ошибок, безопасные сообщения, базовое исключение PriceWatcherError.

### pricewatch.application
- Путь: `src/pricewatch/application/`
- Отвечает: сценарии и интерфейсы репозиториев
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_package_layout.py`

### pricewatch.persistence
- Путь: `src/pricewatch/persistence/`
- Отвечает: модели БД, репозитории, миграции
- Зависит от: `pricewatch.config`, `pricewatch.domain`, `pricewatch.observability`
- Тесты: `uv run pytest tests/integration/test_database_connection.py tests/integration/test_migrations.py tests/integration/test_schema_constraints.py`
- Файлы: `src/pricewatch/persistence/database.py` — подключение: малый пул, hide_parameters, DB_ERROR при недоступной базе.
- Файлы: `src/pricewatch/persistence/models.py` — таблицы users, categories, groups, items; составные ключи владельца; имена ограничений по правилу.
- Файлы: `src/pricewatch/persistence/migrations/`, `src/pricewatch/persistence/migrations/versions/` — миграции Alembic; env.py берёт адрес базы из настроек (config) и пишет журнал в JSON (observability).
- Файлы: `src/pricewatch/persistence/schema.py` — настройка Alembic из кода, без alembic.ini.

### pricewatch.api
- Путь: `src/pricewatch/api/`
- Отвечает: маршруты `/api/v1`, `/web`, `/worker/v1`, `/internal/v1`; аутентификация
- Зависит от: `pricewatch.config`, `pricewatch.domain`, `pricewatch.observability`
- Тесты: `uv run pytest tests/unit/test_api_app.py tests/security/test_client_ip.py tests/integration/test_server_start.py`
- Файлы: `src/pricewatch/api/app.py` — create_app: обработчики ошибок, CORS только своему домену, без автодокументации.
- Файлы: `src/pricewatch/api/edge.py` — внешний слой: request_id, защитные заголовки, 500 без утечек, журнал запросов без строки запроса.
- Файлы: `src/pricewatch/api/errors.py` — HTTP-статус каждого кода ошибки, тело по схеме ErrorResponse.
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
- Зависит от: —
- Тесты: `uv run pytest tests/unit/test_package_layout.py`

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
- Файлы: `src/pricewatch/observability/json_log.py` — JsonFormatter и configure_logging: одна JSON-строка на событие в stdout.
- Файлы: `src/pricewatch/observability/redaction.py` — вырезание секретов из полей и текста, в том числе из traceback.
- Файлы: `src/pricewatch/observability/ids.py` — request_id в контексте выполнения, генератор error_id.

### pricewatch.bootstrap
- Путь: `src/pricewatch/bootstrap/`
- Отвечает: сборка приложения, связывает сценарии с репозиториями; без бизнес-логики
- Зависит от: `pricewatch.api`, `pricewatch.config`, `pricewatch.observability`
- Тесты: `uv run pytest tests/integration/test_server_start.py`
- Файлы: `src/pricewatch/bootstrap/server.py`, `src/pricewatch/bootstrap/__main__.py` — настройки, журнал, приложение, uvicorn.

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

Ожидает: «адаптеры магазинов не импортируют друг друга» — добавляется с первым адаптером на этапе 5.

## Инструменты

- Файлы: `tools/deps.py` — прямые зависимые модуля.
- Тесты: `uv run pytest tests/unit/test_tools_deps.py`
- Файлы: `tools/check_project_map.py` — проверка этой карты.
- Тесты: `uv run pytest tests/unit/test_check_project_map.py`

## Тесты

- Файлы: `tests/unit/` — unit-тесты.
- Файлы: `tests/integration/` — запуск настоящего процесса сервера.
- Файлы: `tests/security/` — набор безопасности; обязателен при изменении входа, ролей, URL, worker (`AGENTS.md`, раздел 7).
- Команда: `uv run pytest tests/security` — только набор безопасности.
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
- Файлы: `design_reference/screenshots/desktop/добавление товара.png` — форма добавления товара, компьютер.
- Файлы: `design_reference/screenshots/mobile/Настройки категорий.PNG` — настройки категорий, телефон.

Без образца, по `SITE_DESIGN_SPEC.md`: группа, вкладки администратора, вход и регистрация.

## Данные и API

- Файлы: `openapi/v1.yaml` — контракт /api/v1 для приложения, утверждён. Меняется только с согласия владельца; совместимо — новыми необязательными полями.
- Тесты: `uv run pytest tests/contracts/test_openapi_document.py`

Схема базы: этап 2 — users, categories, groups, items (`persistence/models.py`); остальные таблицы раздела 5 добавляются миграциями своих этапов. Маршруты `/web`, `/worker/v1`, `/internal/v1` в контракт приложения не входят.

## Сторонние материалы и готовые решения

Готовые решения с GitHub пока не выбирались (`AGENTS.md`, раздел 8).

Шрифты, лицензия SIL OFL 1.1, источник — npm-пакеты @fontsource-variable:
- Файлы: `src/pricewatch/web/static/fonts/unbounded.LICENSE.txt` — Unbounded 5.3.0, github.com/googlefonts/unbounded.
- Файлы: `src/pricewatch/web/static/fonts/onest.LICENSE.txt` — Onest 5.3.1, github.com/simpals/onest.
- Файлы: `src/pricewatch/web/static/fonts/jetbrains-mono.LICENSE.txt` — JetBrains Mono 5.3.0, github.com/JetBrains/JetBrainsMono.
