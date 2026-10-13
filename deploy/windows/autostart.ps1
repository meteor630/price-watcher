# Автозапуск worker при входе в Windows: задача Планировщика «Price Watcher worker» от текущего пользователя, без прав
# администратора. Chrome worker работает с окном (за краем экрана), поэтому нужен вход в систему.
#   autostart.ps1          — создать задачу и запустить worker сейчас;
#   autostart.ps1 -Status  — есть ли задача и запущен ли worker;
#   autostart.ps1 -Stop    — остановить worker (задача остаётся, при следующем входе он запустится);
#   autostart.ps1 -Remove  — остановить worker и удалить задачу.
param([switch]$Status, [switch]$Stop, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$taskName = 'Price Watcher worker'
$profiles = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path '.browser-profiles'

function Get-WorkerProcesses {
    # PowerShell со start-worker.ps1, uv и Python worker, Chrome с профилями магазинов из этого репозитория.
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -like '*pricewatch.worker*' -or $_.CommandLine -like '*start-worker.ps1*' -or $_.CommandLine -like "*$profiles*"
    }
}

function Stop-Worker {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { Stop-ScheduledTask -TaskName $taskName }
    # Задача останавливает только PowerShell; uv, Python и Chrome под ним завершаются отдельно.
    foreach ($process in Get-WorkerProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

if ($Status) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { Write-Host "Задача автозапуска: есть ($($task.State))" } else { Write-Host 'Задача автозапуска: нет' }
    $workers = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -like '*pricewatch.worker*' })
    if ($workers.Count -gt 0) { Write-Host "Worker: работает (процесс $($workers[0].ProcessId))" } else { Write-Host 'Worker: не запущен' }
    exit 0
}
if ($Stop) {
    Stop-Worker
    Write-Host 'Worker остановлен.'
    exit 0
}
if ($Remove) {
    Stop-Worker
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
    Write-Host 'Worker остановлен, задача автозапуска удалена.'
    exit 0
}

$script = Join-Path $PSScriptRoot 'start-worker.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
# Без ограничения времени работы, на батарее тоже; второй экземпляр не запускается.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Host "Задача «$taskName» создана, worker запущен. Состояние: deploy\windows\autostart.ps1 -Status; журнал: logs\worker.log"
