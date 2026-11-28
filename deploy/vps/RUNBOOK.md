# Арендный сервер: подъём с нуля, перенос данных, откат

Сервер — любой VPS с Ubuntu 24.04+ (сейчас Aeza, 1 vCPU, 4 ГБ, диск 10 ГБ). Сайт — `https://prices.meteor630.ru`.
Команды `ssh pw-edge` выполняются с ПК администратора (ключ `~/.ssh/pw_edge` в ssh-agent).

## 1. Новый сервер (≈10 минут)

1. Заказать VPS с Ubuntu 24.04+ и SSH-ключом `pw_edge.pub`. Проверить отпечаток ключа сервера в консоли провайдера:
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` — должен совпасть с тем, что показывает `ssh` при первом входе.
2. DNS: запись `A prices` → IP сервера (TTL 600).
3. Скопировать и запустить установку (под root, IP — внешний IP администратора для SSH):
   ```
   scp deploy/vps/setup.sh root@<IP>:/root/ && ssh root@<IP> sh /root/setup.sh <мой IP> main
   ```
   После этого вход только `ssh meteor@<IP>`; root и пароли по SSH выключены. Сайт стартует с пустой базой.
4. Перенести данные (раздел 2) или восстановить из копии (раздел 3).

## 2. Перенос с домашнего узла (простой ≈5 минут)

Домашняя база — точка отката: до подтверждения владельца её не удалять и не менять.

1. Остановить worker и планировщик дома, чтобы база не менялась:
   `cd deploy/home && docker compose stop scheduler` и `powershell -File deploy\windows\autostart.ps1 -Stop`.
2. Дамп базы и картинок дома:
   ```
   docker compose exec -T db pg_dump -U pricewatch -d pricewatch -Fc > pw.dump
   docker run --rm -v pricewatch_media:/m alpine tar -C /m -cf - . > media.tar
   ```
3. Ключ сервера — тот же, что дома (веб-сессии и токены приложения и worker остаются рабочими):
   `scp .env pw-edge:/tmp/server.env` → `sudo install -m 600 /tmp/server.env /srv/secrets/pricewatch/server.env && rm /tmp/server.env`.
4. На сервер и восстановление:
   ```
   scp pw.dump media.tar pw-edge:/tmp/
   ssh pw-edge 'cd /opt/price-watcher/deploy/vps && C="sudo docker compose --env-file /srv/secrets/pricewatch/compose.env" &&
     $C stop app scheduler && $C exec -T db dropdb -U pricewatch --force pricewatch && $C exec -T db createdb -U pricewatch pricewatch &&
     $C exec -T db pg_restore -U pricewatch -d pricewatch --no-owner < /tmp/pw.dump &&
     $C run --rm --no-deps -v /tmp/media.tar:/media.tar:ro --entrypoint sh app -c "tar -C /data/media -xf /media.tar" &&
     sudo sh up.sh && rm /tmp/pw.dump /tmp/media.tar'
   ```
5. Проверка: `curl -fsS https://prices.meteor630.ru/healthz`, вход на сайт, число товаров совпадает с домашним.
6. Worker дома: в `worker.env` адрес `PRICEWATCH_WORKER_API_URL=https://prices.meteor630.ru`, запустить
   `powershell -File deploy\windows\autostart.ps1`. Токен тот же (его HMAC перенесён вместе с базой).

**Откат** на любом шаге до подтверждения: DNS вернуть нельзя (старого сервера нет), поэтому откат — повторить шаг 4
с новым дампом; домашняя база остаётся нетронутой, worker можно вернуть на `http://127.0.0.1:8000`.

## 3. Резервные копии

- Сервер: планировщик раз в сутки в 04:30 МСК делает копию (pg_dump + картинки → tar → age открытым ключом владельца)
  в том `backups`. Перед копией — проверка места (последняя × 1,5 + 1 ГБ); хранятся две последние. Сбой, нехватка места,
  нет удачной копии 36 часов — уведомление администраторам (колокольчик и бот).
- Дом: контейнер `backup-pull` (`deploy/home`) при старте и ежедневно в 07:00 забирает новые копии по HTTPS
  (`/backups/v1`, токен только на чтение копий), сверяет sha256 и размер, хранит 30 суток в `D:\pricewatch-backups`.
  Дом не забирал копии 36 часов — уведомление администраторам. Приёмник ничего не распаковывает.
- Токен приёмника (один раз; сразу в `pull.env` дома, в чат и журнал не попадает):
  ```
  ssh pw-edge 'cd /opt/price-watcher/deploy/vps && sudo docker compose --env-file /srv/secrets/pricewatch/compose.env exec -T app python -m pricewatch.bootstrap create-backup-reader --name home' | tail -n 1
  ```
  Ротация с перекрытием — `rotate-backup-reader-token --name home`: старый действует, пока приёмник не придёт с новым.
- Проверка восстановления — раз в месяц вручную на ПК владельца (ключ age — только у владельца, никуда не копируется):
  ```
  powershell -ExecutionPolicy Bypass -File deploy\windows\restore-check.ps1 -Key <файл ключа age>
  ```
  Берёт самую новую копию из `D:\pricewatch-backups`, восстанавливает во временный PostgreSQL без сети и томов и печатает
  число товаров, ссылок, цен и картинок.

## 4. Восстановление из зашифрованной копии

Копии `pricewatch-*.tar.age` (дома — `D:\pricewatch-backups`) зашифрованы открытым ключом age; закрытый — только у владельца.
Расшифровать на ПК владельца (ключ на сервер не попадает): `age -d -i <ключ> pricewatch-....tar.age > pw.tar`,
`tar -xf pw.tar` → `database.dump` и папка картинок; дальше — шаг 4 раздела 2 с этим `database.dump`.
Автоматического восстановления нет: зашифровать открытым ключом может кто угодно, в том числе захваченный сервер.

## 5. Обновление кода

`ssh pw-edge 'cd /opt/price-watcher && git pull --ff-only && sudo sh deploy/vps/up.sh'`
