# Проверка восстановления резервной копии (раз в месяц, вручную, на ПК владельца).
# Копия расшифровывается закрытым ключом age владельца и восстанавливается во временный PostgreSQL без сети и без
# томов основной системы (данные в памяти контейнера); печатается число товаров, ссылок, цен и картинок.
# Ничего не меняет ни на сервере, ни в домашних копиях; временные файлы и контейнер удаляются в конце.
#   powershell -ExecutionPolicy Bypass -File deploy\windows\restore-check.ps1 -Key <файл ключа age> [-Backup <копия>]
# Без -Backup берётся самая новая копия из BACKUP_HOST_DIR (deploy\home\.env). Ключ не копируется и не печатается.
param(
    [Parameter(Mandatory = $true)][string]$Key,
    [string]$Backup
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$postgres = 'postgres:18@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722'
$container = 'pw-restore-check'

function Invoke-Checked([string]$what, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$what — код $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $Key)) { throw "Нет файла ключа: $Key" }
if (-not $Backup) {
    $envFile = Join-Path $root 'deploy\home\.env'
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^BACKUP_HOST_DIR=' } | Select-Object -First 1
    if (-not $line) { throw "В $envFile нет BACKUP_HOST_DIR; укажите копию параметром -Backup" }
    $folder = $line.Substring('BACKUP_HOST_DIR='.Length)
    $newest = Get-ChildItem -LiteralPath $folder -Filter 'pricewatch-*.tar.age' | Sort-Object Name | Select-Object -Last 1
    if (-not $newest) { throw "В $folder нет копий" }
    $Backup = $newest.FullName
}
Write-Output "Копия: $Backup"

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("pw-restore-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    Invoke-Checked 'Копия не расшифрована (неверный ключ или повреждённый файл)' { age --decrypt --identity $Key --output (Join-Path $work 'copy.tar') $Backup }
    Invoke-Checked 'Архив не распакован' { tar -xf (Join-Path $work 'copy.tar') -C $work }
    $dump = Join-Path $work 'database.dump'
    if (-not (Test-Path -LiteralPath $dump)) { throw 'В копии нет database.dump' }
    $media = @(Get-ChildItem -LiteralPath (Join-Path $work 'media') -Recurse -File -ErrorAction SilentlyContinue).Count

    $password = [guid]::NewGuid().ToString('N')
    Invoke-Checked 'Временный PostgreSQL не запущен' { docker run -d --rm --name $container --network none --tmpfs /var/lib/postgresql -e "POSTGRES_PASSWORD=$password" $postgres | Out-Null }
    $ready = $false
    for ($i = 0; $i -lt 60 -and -not $ready; $i++) {
        docker exec $container pg_isready -U postgres -q 2>$null
        $ready = ($LASTEXITCODE -eq 0)
        if (-not $ready) { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw 'Временный PostgreSQL не ответил за 60 секунд' }
    Invoke-Checked 'Дамп не скопирован в контейнер' { docker cp $dump "${container}:/tmp/database.dump" }
    Invoke-Checked 'База не создана' { docker exec $container createdb -U postgres pricewatch }
    Invoke-Checked 'pg_restore завершился с ошибкой' { docker exec $container pg_restore -U postgres -d pricewatch --no-owner --exit-on-error /tmp/database.dump }
    $counts = docker exec $container psql -U postgres -d pricewatch -tA -F ' ' -c "SELECT (SELECT count(*) FROM items), (SELECT count(*) FROM tracked_links), (SELECT count(*) FROM price_observations), (SELECT max(checked_at) FROM price_observations)"
    if ($LASTEXITCODE -ne 0) { throw 'Счётчики не прочитаны' }
    $parts = "$counts".Trim().Split(' ', 4)
    Write-Output "Восстановление прошло: товаров $($parts[0]), ссылок $($parts[1]), цен $($parts[2]), последняя цена $($parts[3]), картинок $media."
}
finally {
    docker rm -f $container 2>$null | Out-Null
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
