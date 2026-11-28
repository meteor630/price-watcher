# Запуск worker на Windows: из корня репозитория, журнал — logs\worker.log (больше 10 МБ — в worker.log.1).
# Упавший worker (сбой Chrome или Python) перезапускается через 30 секунд. Не перезапускается, если он остановлен (код 0),
# если неверны настройки (код 2) или сервер не принял токен (код 4): повтор не поможет, нужна правка worker.env.
# Сервер недоступен — worker сам ждёт и повторяет, это не сбой.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$running = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like '*pricewatch.worker*' }
if ($running) {
    # Два worker с одним токеном мешали бы друг другу.
    Write-Host "Worker уже запущен (процесс $($running[0].ProcessId)). Остановить: deploy\windows\autostart.ps1 -Stop"
    exit 1
}
Set-Location $root
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs 'worker.log'
$restartDelaySeconds = 30

while ($true) {
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 10MB) { Move-Item -Force $log "$log.1" }
    # Через cmd: журнал пишется байтами как есть (UTF-8 JSON), без перекодировки PowerShell.
    cmd /c "uv run --no-dev --locked python -m pricewatch.worker >> ""$log"" 2>&1"
    $code = $LASTEXITCODE
    if ($code -in 0, 2, 4, 5, 6) {
        Write-Host "Worker завершился с кодом $code; журнал: $log"
        exit $code
    }
    Write-Host "Worker упал с кодом $code; перезапуск через $restartDelaySeconds с. Журнал: $log"
    Start-Sleep -Seconds $restartDelaySeconds
}
