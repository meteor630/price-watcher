# PriceWatcher для iPhone

Откройте `PriceWatcher.xcodeproj` в Xcode. Цель `PriceWatcher` поддерживает iPhone с iOS 17 и новее. Для установки на телефон выберите свою команду в **Signing & Capabilities** и устройство в списке целей Xcode.
Данные, история и правила расчёта приходят из существующего `/api/v1`. Пользователь подключается одноразовым кодом из **Настройки → Профиль → Получить код** на сайте.

Вкладки **Обзор**, **Покупки** и **Управление** написаны на SwiftUI. Кнопка **+** в обзоре или категории открывает форму товара. Через многоточие в карточке товара можно изменить название, количество и ссылки, записать покупку, архивировать или удалить товар. В управлении доступны каталог, группы, проверка изменений и настройки. Вход по коду даёт доступ к функциям согласно роли аккаунта.

## Сервер в Docker

Из корня репозитория. Если Terminal на Mac не видит команду Docker Desktop, добавьте его CLI для текущего сеанса командой `export PATH="$HOME/.docker/bin:$PATH"`.

1. Скопируйте `deploy/local/.env.example` в `deploy/local/.env`. На Mac можно выполнить `cp deploy/local/.env.example deploy/local/.env`, в PowerShell — `Copy-Item deploy/local/.env.example deploy/local/.env`. Задайте два разных случайных шестнадцатеричных значения для пароля PostgreSQL и ключа PriceWatcher. Для нового ключа можно использовать `openssl rand -hex 48`; в PowerShell 7 подойдёт `[Convert]::ToHexString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(48))`.
2. Для симулятора запустите `docker compose -f deploy/local/compose.yaml up -d --build --wait app`. В приложении используйте `http://localhost:8000`.
3. Для телефона в домашней Wi‑Fi сети задайте `PRICEWATCH_LAN_IP` и `PRICEWATCH_PUBLIC_ORIGIN` в `deploy/local/.env`. Например, при IP компьютера `192.168.1.20` укажите `PRICEWATCH_LAN_IP=192.168.1.20` и `PRICEWATCH_PUBLIC_ORIGIN=http://192.168.1.20:8000`. Запустите `docker compose -f deploy/local/compose.yaml -f deploy/local/compose.lan.yaml up -d --build --wait app`. Разрешите PriceWatcher доступ к локальной сети на iPhone. В Windows при включённом брандмауэре порт 8000 должен быть доступен в профиле частной сети. Для подключения через интернет используйте действующий HTTPS-адрес сервера.
4. Для пустой базы создайте администратора командой `docker compose -f deploy/local/compose.yaml exec app python -m pricewatch.bootstrap create-admin --login ВАШ_ЛОГИН`. Откройте адрес сервера на сайте и найдите код в профиле. В приложении введите тот же адрес и код.

Для запуска здесь же по расписанию добавьте сервис `scheduler` в команду запуска. Парсер использует существующие команды из `deploy/windows`: выпустите токен через `docker compose -f deploy/local/compose.yaml exec app python -m pricewatch.bootstrap create-worker --name home`, заполните локальный `worker.env` и запустите `deploy/windows/start-worker.ps1`. При стандартном порте адрес worker — `http://127.0.0.1:8000`; публикация в Wi‑Fi оставляет этот адрес доступным.

## Подключение через мобильный интернет

Код из профиля привязывает iPhone к введённому адресу. Чтобы открыть PriceWatcher из другой сети, сначала запустите тот же сервер с доступным извне HTTPS-адресом. Для схемы с домашним компьютером и VPS конфигурация лежит в `deploy/home` и `deploy/vps`. Пересоберите Docker на публичном сервере с этой версией проекта. Откройте HTTPS-адрес на iPhone через мобильную сеть и проверьте, что загружается форма входа. Затем войдите на сайт по этому адресу, получите новый код в профиле и введите HTTPS-адрес и код в приложении. Для смены адреса с уже подключённого телефона откройте **Обзор → Профиль → Выйти**.

## Первый запуск на Windows в домашней сети

### 1. Скачайте проект и подготовьте Docker

1. Установите [GitHub Desktop](https://desktop.github.com/download/) и войдите в аккаунт, у которого есть доступ к приватному репозиторию.
2. В GitHub Desktop откройте **File → Clone repository → GitHub.com**, выберите `meteor630/price-watcher` и укажите папку `PriceWatcher` сразу внутри своей папки пользователя Windows. Нажмите **Clone**. Затем откройте **Current Branch** и выберите `ios/pricewatcher-iphone`. Если проект уже скачан, выберите эту ветку и нажмите **Fetch origin**, затем **Pull origin**, когда кнопка появится.
3. Установите [Docker Desktop для Windows](https://docs.docker.com/desktop/setup/install/windows-install/), выберите WSL 2 и запустите Docker Desktop. Если WSL ещё не включён, выполните `wsl --install --no-distribution` в PowerShell от администратора и перезагрузите компьютер. Дождитесь запуска Docker и проверьте `docker compose version`. [Команды WSL](https://learn.microsoft.com/en-us/windows/wsl/basic-commands)
4. Перенесите файл `pricewatch-2026-11-22-0019.dump` в папку **Загрузки** на Windows. Если после этой копии на Mac менялись товары или цены, сначала сделайте свежий дамп и укажите его имя на шаге 3. [Как сохранить актуальную базу с Mac](#актуальная-копия-базы-с-mac)

### 2. Создайте настройки Windows-сервера

В обычном PowerShell выполните `ipconfig` и запомните **IPv4-адрес активного Ethernet или Wi-Fi**. Откройте PowerShell и вставьте блок. По запросу введите этот адрес, например `192.168.1.25`. Выполняйте блок один раз на новой установке.

```powershell
Set-Location "$env:USERPROFILE\PriceWatcher"
if (-not (Test-Path 'deploy/local/compose.yaml')) { throw 'Откройте папку клона PriceWatcher.' }
if (Test-Path 'deploy/local/.env') { throw 'Файл .env уже есть. Продолжайте с готовыми настройками.' }
$pcIp = Read-Host 'Локальный IPv4-адрес Windows из ipconfig'
$random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $bytes = New-Object byte[] 48
    $random.GetBytes($bytes)
    $dbPassword = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    $random.GetBytes($bytes)
    $secretKey = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
} finally {
    $random.Dispose()
}
@(
    "POSTGRES_PASSWORD=$dbPassword"
    "PRICEWATCH_SECRET_KEY=$secretKey"
    "PRICEWATCH_LAN_IP=$pcIp"
    "PRICEWATCH_PUBLIC_ORIGIN=http://${pcIp}:8000"
) | Set-Content 'deploy/local/.env' -Encoding Ascii
```

### 3. Восстановите базу и запустите сайт

В PowerShell из папки проекта выполните блок. Для свежего дампа замените имя файла в строке `$dump`. Сначала поднимается только PostgreSQL. Дамп восстанавливается целиком в пустую базу, а сайт применяет миграции при старте.

```powershell
Set-Location "$env:USERPROFILE\PriceWatcher"
$compose = @('-f', 'deploy/local/compose.yaml', '-f', 'deploy/local/compose.lan.yaml')
$dump = "$env:USERPROFILE\Downloads\pricewatch-2026-11-22-0019.dump"
if (-not (Test-Path -LiteralPath $dump)) { throw "Файл не найден: $dump" }
docker compose @compose up -d --wait db
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL не запустился.' }
$tables = docker compose @compose exec -T db psql -X -U pricewatch -d pricewatch -Atc "SELECT count(*) FROM pg_tables WHERE schemaname='public';"
if ($LASTEXITCODE -ne 0 -or "$tables".Trim() -ne '0') { throw 'Для восстановления нужна пустая база.' }
docker compose @compose cp $dump 'db:/tmp/pricewatch-import.dump'
if ($LASTEXITCODE -ne 0) { throw 'Не удалось скопировать дамп в контейнер.' }
docker compose @compose exec -T db pg_restore --single-transaction --no-owner --no-privileges -U pricewatch -d pricewatch /tmp/pricewatch-import.dump
if ($LASTEXITCODE -ne 0) { throw 'Восстановление не прошло. Проверьте сообщение PostgreSQL.' }
docker compose @compose exec -T db rm /tmp/pricewatch-import.dump
docker compose @compose up -d --build --wait app
if ($LASTEXITCODE -ne 0) { throw 'Сайт не запустился.' }
Invoke-RestMethod 'http://localhost:8000/healthz'
```

Должно появиться `status ok`. Для входа и выдачи кода на новом Windows-сервере используйте локальную форму из шага 4. Дамп и локальный `.env` храните вне Git. [Восстановление PostgreSQL](https://www.postgresql.org/docs/18/app-pgrestore.html)

### 4. Переключите iPhone на Windows

1. Держите Mac доступным, пока переключаете iPhone. В приложении откройте **Профиль → Выйти**, чтобы завершить сессию на Mac.
2. Откройте форму входа на самом Windows-компьютере. В PowerShell из папки проекта временно запустите сайт с адресом `localhost`, чтобы браузер принял защищённую веб-сессию.

   ```powershell
   $compose = @('-f', 'deploy/local/compose.yaml', '-f', 'deploy/local/compose.lan.yaml')
   $env:PRICEWATCH_PUBLIC_ORIGIN = 'http://localhost:8000'
   docker compose @compose up -d --wait app
   ```

   В браузере откройте `http://localhost:8000`, войдите и выберите **Настройки → Профиль → Получить код**. Сразу верните адрес из `deploy/local/.env`.

   ```powershell
   Remove-Item Env:PRICEWATCH_PUBLIC_ORIGIN
   docker compose @compose up -d --wait app
   ```

3. На iPhone в той же сети введите адрес **Windows** в PriceWatcher и этот одноразовый код. Если iPhone не открывает адрес Windows, проверьте, что домашняя сеть в Windows имеет профиль **Частная**. Если порт заблокирован брандмауэром, откройте его только для локальной подсети из PowerShell от администратора. [Правила брандмауэра Windows](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule?view=windowsdefender)

```powershell
New-NetFirewallRule -DisplayName 'PriceWatcher LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private -RemoteAddress LocalSubnet
```

### 5. Запустите парсер с Chrome

Для просмотра сохранённых цен достаточно Docker. Для сбора новых на Windows установите Chrome, если его ещё нет, и `uv`. [Установка uv](https://docs.astral.sh/uv/getting-started/installation/)

```powershell
winget install -e --id Google.Chrome
winget install -e --id astral-sh.uv
```

Откройте новое окно PowerShell и выполните команды. Скопируйте выданный токен в строку `PRICEWATCH_WORKER_TOKEN=` в открывшемся Блокноте, сохраните файл и закройте его.

```powershell
Set-Location "$env:USERPROFILE\PriceWatcher"
$compose = @('-f', 'deploy/local/compose.yaml', '-f', 'deploy/local/compose.lan.yaml')
if (Test-Path 'worker.env') { throw 'worker.env уже есть. Проверьте в нём адрес и действующий токен.' }
docker compose @compose exec app python -m pricewatch.bootstrap create-worker --name windows-pc
if ($LASTEXITCODE -ne 0) { throw 'Не удалось выпустить токен worker.' }
Copy-Item worker.env.example worker.env
notepad worker.env
```

Затем установите зависимости и запустите worker в этом же окне. Пока идёт сбор, держите окно открытым. Кнопка **«Обновить цены»** доступна администратору на сайте и в приложении.

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-worker.ps1
powershell -ExecutionPolicy Bypass -File deploy\windows\start-worker.ps1
```

При следующем запуске компьютера откройте Docker Desktop и выполните команды ниже. После обновления проекта через GitHub Desktop добавьте `--build` к команде Docker, чтобы пересобрать сервер. Существующий том PostgreSQL содержит восстановленные данные. Для проверок по расписанию добавьте `scheduler` после `app` в команду запуска.

```powershell
Set-Location "$env:USERPROFILE\PriceWatcher"
docker compose -f deploy/local/compose.yaml -f deploy/local/compose.lan.yaml up -d --wait app
powershell -ExecutionPolicy Bypass -File deploy\windows\start-worker.ps1
```

### Актуальная копия базы с Mac

В Терминале Mac выполните команды. В папке **Загрузки** появится `pricewatch-current.dump`, который можно перенести на Windows и указать на шаге 3.

```sh
cd "/Users/meteor630/Documents/Проекты/Price-Watcher/deploy/local"
export PATH="$HOME/.docker/bin:$PATH"
docker compose -f compose.yaml -f compose.lan.yaml exec -T db pg_dump -U pricewatch -Fc pricewatch > "$HOME/Downloads/pricewatch-current.dump"
```

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

В схеме `PriceWatcher` есть тесты декодирования контракта, адреса сервера и Keychain, а также сценарии входа на симуляторе. Для сквозного теста на отдельном Docker-проекте передайте в среду тестового раннера `PRICEWATCH_TEST_CODE` и при необходимости `PRICEWATCH_TEST_SERVER`, `PRICEWATCH_TEST_CATEGORY` и `PRICEWATCH_TEST_PRODUCT`. Используйте тестовую учётную запись. Сценарий сохраняет изменение отметки товара в базе и отзывает устройство на выходе. Чтобы проверить нативное добавление и удаление товара, передайте `PRICEWATCH_TEST_MANAGEMENT=1` для тестовой учётной записи с категорией.
