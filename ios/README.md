# PriceWatcher для iPhone

Откройте `PriceWatcher.xcodeproj` в Xcode. Цель `PriceWatcher` поддерживает iPhone с iOS 17 и новее. Для установки на телефон выберите свою команду в **Signing & Capabilities** и устройство в списке целей Xcode.
Данные, история и правила расчёта приходят из существующего `/api/v1`. Пользователь подключается одноразовым кодом из **Настройки → Профиль → Получить код** на сайте.

## Сервер в Docker

Из корня репозитория. Если Terminal на Mac не видит команду Docker Desktop, добавьте его CLI для текущего сеанса командой `export PATH="\$HOME/.docker/bin:\$PATH"`.

1. Скопируйте `deploy/local/.env.example` в `deploy/local/.env`. На Mac можно выполнить `cp deploy/local/.env.example deploy/local/.env`, в PowerShell — `Copy-Item deploy/local/.env.example deploy/local/.env`. Задайте два разных случайных шестнадцатеричных значения для пароля PostgreSQL и ключа PriceWatcher. Для нового ключа можно использовать `openssl rand -hex 48`; в PowerShell 7 подойдёт `[Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(48))`.
2. Для симулятора запустите `docker compose -f deploy/local/compose.yaml up -d --build --wait app`. В приложении используйте `http://localhost:8000`.
3. Для телефона в домашней Wi‑Fi сети задайте `PRICEWATCH_LAN_IP` и `PRICEWATCH_PUBLIC_ORIGIN` в `deploy/local/.env`. Например, при IP компьютера `192.168.1.20` укажите `PRICEWATCH_LAN_IP=192.168.1.20` и `PRICEWATCH_PUBLIC_ORIGIN=http://192.168.1.20:8000`. Запустите `docker compose -f deploy/local/compose.yaml -f deploy/local/compose.lan.yaml up -d --build --wait app`. Разрешите PriceWatcher доступ к локальной сети на iPhone. В Windows при включённом брандмауэре порт 8000 должен быть доступен в профиле частной сети. Для подключения через интернет используйте действующий HTTPS-адрес сервера.
4. Для пустой базы создайте администратора командой `docker compose -f deploy/local/compose.yaml exec app python -m pricewatch.bootstrap create-admin --login ВАШ_ЛОГИН`. Откройте адрес сервера на сайте и найдите код в профиле. В приложении введите тот же адрес и код.

Для запуска здесь же по расписанию добавьте сервис `scheduler` в команду запуска. Парсер использует существующие команды из `deploy/windows`: выпустите токен через `docker compose -f deploy/local/compose.yaml exec app python -m pricewatch.bootstrap create-worker --name home`, заполните локальный `worker.env` и запустите `deploy/windows/start-worker.ps1`. При стандартном порте адрес worker — `http://127.0.0.1:8000`; публикация в Wi‑Fi оставляет этот адрес доступным.

## История со старого компьютера

Сначала проверьте копию базы в отдельном проекте Compose, указав `-p pricewatch-migration` и свободный локальный порт через `PRICEWATCH_PORT`. Для старого SQLite существует `tools/import_legacy.py`. Он переносит данные в одной транзакции и по умолчанию отказывается писать в учётную запись, где уже есть категории. Сделайте копию SQLite с помощью команды `.backup` SQLite, чтобы включить сохранённые изменения из WAL.

После запуска и создания владельца выполните из корня репозитория. Вместо пути и логина укажите свои значения.

```sh
docker compose -p pricewatch-migration -f deploy/local/compose.yaml run --rm --no-deps \
  -v "${PWD}/tools:/tools:ro" -v "/путь/к/папке/с/копией:/import:ro" app \
  python /tools/import_legacy.py /import/pricewatch.db --login ВАШ_ЛОГИН
```

Для дампа PostgreSQL восстановите дамп в отдельную пустую базу той же версии, примените миграции Alembic и проверьте список товаров и графики. Импорт SQLite и восстановление PostgreSQL выполняются на сервере. Файлы базы и `.env` храните вне Git.

## Проверки

В схеме `PriceWatcher` есть тесты декодирования контракта, адреса сервера и Keychain, а также сценарии входа на симуляторе. Для сквозного теста на отдельном Docker-проекте передайте в среду тестового раннера `PRICEWATCH_TEST_CODE` и при необходимости `PRICEWATCH_TEST_SERVER`, `PRICEWATCH_TEST_CATEGORY` и `PRICEWATCH_TEST_PRODUCT`. Используйте тестовую учётную запись. Сценарий сохраняет изменение отметки товара в базе и отзывает устройство на выходе.
