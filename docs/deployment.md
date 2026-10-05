# Действующее развёртывание — 5 октября 2026

Установка выполнена после разрешения пользователя через существующий `harp_proxy_docker`. Nextcloud, HaRP, ONLYOFFICE и другие ExApp не останавливались. Снимки ID/StartedAt контейнеров до/после совпадают; `status.php` возвращает `maintenance=false`, `needsDbUpgrade=false`.

Административный интерфейс: [Мероприятия](https://nc.imc-mosk.ru/apps/app_api/embedded/exapp_events/events/). Нужна обычная сессия администратора Nextcloud.

## Состав

| Объект | Значение |
| --- | --- |
| Репозиторий | `/opt/nextcloud/exapps/exapp-events/`, origin `kyasu-404/exapp-events` |
| ExApp | `exapp_events`, версия 0.1.1, enabled |
| Контейнер | `nc_app_exapp_events`, healthy |
| Image | `events.local/kyasu-404/exapp-events:0.1.1` |
| Image ID первой установки | `sha256:03eb4af996212b6b66188677b2c34e6128c44d358a14fd03a54e5e38a32de5ec` |
| Основной код image | commit `896944f` |
| HaRP | существующий контейнер `nc_harp`, без перезапуска |
| Docker network | `nextcloud-net` |
| Restart policy | `unless-stopped` |
| Лимиты новой ExApp | 2 CPU, 1 ГБ RAM, до 2 ГБ RAM+swap |
| Persistent storage | volume `nc_app_exapp_events_data`, путь `/nc_app_exapp_events_data` |
| SQLite | `/nc_app_exapp_events_data/events.sqlite3`, WAL, миграции 1/2 |
| SMTP | `smtp_mode=nextcloud`, уведомления включены |
| SMTP-модуль | `/opt/nextcloud/custom_apps/exapp_events_bridge`, enabled 0.1.0 |

В daemon добавлено только отдельное сопоставление registry `events.local → local`. Оно касается данного образа и не заменяет настройки других registry. DNS/registry-сервис `events.local` не нужен: AppAPI использует уже загруженный локальный Docker image. Cloud compose и сертификаты HaRP хоста не редактировались.

Образ собирался отдельным builder `exapp-events-build`, ограниченным 2 CPU/2 ГБ RAM. Builder можно останавливать после сборки; его cache сохраняется. Приложение работает от UID 10001, запускается через supervisor, который наблюдает за FastAPI и FRP.

5 октября приложение обновлено через AppAPI до 0.1.1. Строки с одной аудиторией и пустыми остальными полями теперь пропускаются без предупреждений; неполные мероприятия и формулы без cached values сохраняют защиту от удаления. Перед обновлением выполнен SQLite online backup в private подкаталог `backups` того же persistent volume. После обновления контейнер healthy, версия API 0.1.1, admin API и assets отвечают HTTP 200; настройки источника, календаря и SMTP сохранены. ID/StartedAt всех 21 остальных работающих контейнеров совпали до/после. Backend: 100 тестов прошли, Ruff и Docker build прошли; builder снова остановлен.

## Почему состояние сохраняется

- SQLite, настройки, очередь файловых событий и SMTP находятся в именованном Docker volume. Контейнер приложения можно пересоздать с прежним volume.
- `unless-stopped` восстанавливает ExApp после перезапуска Docker/хоста. При намеренном `docker stop` требуется `docker start`, как и для существующих сервисов.
- FRP переподключается к HaRP. Если FRP или FastAPI завершается, supervisor завершает контейнер, Docker запускает его снова.
- При старте приложение проверяет enabled state в Nextcloud и повторяет подключение без конечного числа попыток, с паузой до 60 секунд. Восстановление не зависит от того, успело ли облако стартовать за две минуты.
- SMTP-модуль находится в уже существующем bind mount `custom_apps` вне image Nextcloud. Он использует актуальную конфигурацию `IMailer`; пароль не хранится в image, Git или SQLite приложения.
- Фоновые обработки зарегистрированы в AppAPI/Webhook Listeners, а периодическая сверка исправляет пропущенные во время простоя файловые события.

Поддержка в XML заявлена для Nextcloud 34/35; установка фактически проверена на 34.0.2. Перед переходом на другую основную версию Nextcloud проверьте совместимость AppAPI/SDK и SMTP-модуля. Само обновление minor-версии/image не удаляет перечисленные volumes/bind mounts.

## Выполненные проверки

1. Docker build с нуля, checksum FRP, lifecycle init/enable, heartbeat и healthcheck.
2. API и JS/CSS возвращают 200 через AppAPI → существующий HaRP → ExApp.
3. Top Menu зарегистрирован с `adminRequired=1`; настоящий обычный пользователь получает 403 на административный API.
4. Четыре файловых listeners зарегистрированы; persistent storage и SQLite исправны.
5. Files root выбранного для smoke-check администратора читается; CalDAV возвращает 3 календаря, 2 доступны для записи. Содержимое файлов/календарей не менялось.
6. SMTP bridge выполняет SMTP/TLS/AUTH/QUIT с настройками Nextcloud успешно. Запрос без подписи получает 401. Письмо никому не отправлялось.
7. Перезапущен только `nc_app_exapp_events`: включённое состояние, настройки SMTP, listeners и доступ восстановлены.
8. Завершён только FRP-процесс новой ExApp: supervisor/Docker автоматически восстановили контейнер, состояние healthy и SMTP probe снова успешны.
9. ID и времена запуска Nextcloud, HaRP, `file_control`, `oo_file_collector` до/после совпали. Остальные контейнеры продолжают работать.
10. 93 backend-теста, 8 frontend-тестов и CI со сборкой Docker проходят. Первый production image соответствует [успешному CI](https://github.com/kyasu-404/exapp-events/actions/runs/37315196220).

Источник XLSX и календарь назначения ещё **не выбраны**: реальные мероприятия не создавались и не удалялись. Поэтому диагностические пункты папки/календаря сейчас показывают отсутствие конфигурации; транспорт CalDAV отдельно проверен на чтение. Администратору нужно выбрать папку и календарь, сохранить настройки, посмотреть dry-run и затем синхронизировать. Доставку письма проверять на собственном контрольном адресе.

## Обновление только этого приложения

Для нового release сначала обновите версии в metadata/package/коде, проверьте изменения и совместимость миграций. Не используйте `app_api:app:update --all`, `docker compose down`, перезапуск общего Nextcloud/HaRP или `docker system prune` для обновления ExApp.

```sh
cd /opt/nextcloud/exapps/exapp-events
git pull --ff-only
docker buildx build --builder exapp-events-build --platform linux/amd64 --load \
  -t events.local/kyasu-404/exapp-events:NEW_VERSION .
python3 scripts/prepare_release.py --registry events.local --image kyasu-404/exapp-events \
  --tag NEW_VERSION --output dist/info.xml
docker cp dist/info.xml nextcloud:/tmp/exapp-events-info.xml
docker exec --user www-data nextcloud php occ app_api:app:update exapp_events \
  --info-xml=/tmp/exapp-events-info.xml --wait-finish
docker update --cpus=2 --memory=1g --memory-swap=2g --restart=unless-stopped nc_app_exapp_events
docker buildx stop exapp-events-build
```

Замените `NEW_VERSION` реальной новой версией, согласованной с `appinfo/info.xml`. AppAPI управляет контейнером и прежним persistent volume; он может пересоздать лимиты из daemon defaults, поэтому `docker update` повторяется только для новой ExApp. Для обновления SMTP-модуля переносите только файлы `nextcloud_bridge/exapp_events_bridge` в соответствующий persistent каталог и обновляйте его metadata. Другие модули не заменяйте.

Перед обновлением делайте согласованную резервную копию SQLite/volume. Для online-копии используйте SQLite Backup API, а не одиночное копирование `.sqlite3` при активном WAL. Храните backup вне рабочего volume, с ограниченными правами; он может содержать email/мероприятия. Не удаляйте `nc_app_exapp_events_data` при очистке старых images. Откат схемы требует соответствующего backup, обратных миграций нет.

## Диагностика

```sh
docker ps --filter name=nc_app_exapp_events
docker logs --tail 100 nc_app_exapp_events
docker exec --user www-data nextcloud php occ app_api:app:list
docker cp scripts/nextcloud_ops.php nextcloud:/tmp/exapp-events-ops.php
docker exec --user www-data nextcloud php /tmp/exapp-events-ops.php status
docker exec --user www-data nextcloud php /tmp/exapp-events-ops.php diagnostics
docker exec --user www-data nextcloud php /tmp/exapp-events-ops.php security
```

Helper использует AppAPI и проверяет только это приложение; не выводит секреты и не отправляет почту. После пересоздания Nextcloud временный helper можно скопировать из репозитория снова; runtime и SMTP-модуль от него не зависят. Журналы сборки/установки и снимки контейнеров лежат в gitignored `dist/` на сервере.
