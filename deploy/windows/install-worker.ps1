# Установка worker на Windows (DEVELOPMENT_ARCHITECTURE.md, раздел 1): зависимости по uv.lock и проверка настроек.
# Запуск из любой папки: powershell -ExecutionPolicy Bypass -File deploy\windows\install-worker.ps1
# Worker — обычная программа Windows, не Docker: Chrome под Linux в Docker защита Ozon не пускает (проверено 13.10.2026).
# Нужны uv (ставит и Python) и Google Chrome; токен и адрес сервера — в worker.env в корне репозитория.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Нет uv. Установите: winget install -e --id astral-sh.uv — затем откройте новое окно PowerShell.'
    exit 1
}
$chrome = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Host 'Нет Google Chrome. Установите: winget install -e --id Google.Chrome'
    exit 1
}

Push-Location $root
try {
    # Только зависимости рабочего кода; Python нужной версии uv скачает сам.
    uv sync --locked --no-dev
    if ($LASTEXITCODE -ne 0) { throw "uv sync завершился с кодом $LASTEXITCODE" }
} finally {
    Pop-Location
}

$envFile = Join-Path $root 'worker.env'
if (-not (Test-Path $envFile)) {
    Write-Host "Нет $envFile. Скопируйте worker.env.example в worker.env и заполните токен и адрес сервера."
    exit 1
}
# Значения не печатаются: токен — секрет.
$filled = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match '^\s*(PRICEWATCH_WORKER_[A-Z_]+)\s*=\s*(\S.*)$') { $filled[$Matches[1]] = $true }
}
$missing = @('PRICEWATCH_WORKER_API_URL', 'PRICEWATCH_WORKER_TOKEN') | Where-Object { -not $filled.ContainsKey($_) }
if ($missing) {
    Write-Host ('В worker.env не заполнено: ' + ($missing -join ', '))
    exit 1
}
Write-Host "Готово. Chrome: $chrome"
Write-Host 'Проверка запуска: powershell -ExecutionPolicy Bypass -File deploy\windows\start-worker.ps1 (остановка — Ctrl+C);'
Write-Host 'автозапуск при входе в Windows: powershell -ExecutionPolicy Bypass -File deploy\windows\autostart.ps1'
