# Действующее развёртывание — 6 октября 2026

Установка выполнена после разрешения пользователя через существующий `harp_proxy_docker`. Nextcloud, HaRP, ONLYOFFICE и другие ExApp не останавливались. Снимки ID/StartedAt контейнеров до/после совпадают; `status.php` возвращает `maintenance=false`, `needsDbUpgrade=false`.

Административный интерфейс: [Мероприятия](https://nc.imc-mosk.ru/apps/app_api/embedded/exapp_events/events/). Нужна обычная сессия администратора Nextcloud.

## Состав

| Объект | Значение |
| --- | --- |
| Репозиторий | `/opt/nextcloud/exapps/exapp-events/`, origin `kyasu-404/exapp-events` |
| ExApp | `exapp_events`, версия 0.1.3, enabled |
| Контейнер | `nc_app_exapp_events`, healthy |
| Image | `events.local/kyasu-404/exapp-events:0.1.3` |
| Image ID первой установки | `sha256:03eb4af996212b6b66188677b2c34e6128c44d358a14fd03a54e5e38a32de5ec` |
| Основной код image | commit `214566c` |
| HaRP | существующий контейнер `nc_harp`, без перезапуска |
| Docker network | `nextcloud-net` |
| Restart policy | `unless-stopped` |
| Лимиты новой ExApp | 2 CPU, 1 ГБ RAM, до 2 ГБ RAM+swap |
| Persistent storage | volume `nc_app_exapp_events_data`, путь `/nc_app_exapp_events_data` |
| SQLite | `/nc_app_exapp_events_data/events.sqlite3`, WAL, миграции 1/2 |
| SMTP | `smtp_mode=nextcloud`, уведомления включены |
| SMTP-модуль | `/opt/nextcloud/custom_apps/exapp_events_bridge`, enabled 0.1.0 |
| Синхронизация | автоматическая, `delete_guard=false` |
| Архивная папка | пока не задана; выбирается в «Источник данных» |

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

При первой установке источник и календарь не были выбраны. Сейчас пользователь настроил источник `0. Планы ИМЦ/` и внутренний календарь. После замены XLSX изменились ID файлов: план создавал 85 событий и удалял 39 старых. На 0.1.3 после отключения подтверждения этот план применился автоматически без ошибок Calendar.

## Исправление напоминаний 6 октября

На 0.1.1 ожидание подтверждения календарного плана блокировало также сохранение Excel-состояния и очередь SMTP. Напоминание 06.10 в 13:00 для события в 15:00 было прочитано заранее, но так и не попало в очередь. Тестовое письмо использовало непосредственный вызов того же исправного SMTP-моста.

На 0.1.2 проверенное состояние Excel и задания писем сохраняются независимо от подтверждения календаря. Повторная сверка не дублирует письма и сохраняет актуальность подтверждения; проверка ETag перед записью остаётся. Исправлено сопоставление пути папки без начального `/` в webhook новых файлов. Просроченное до постановки в очередь задание сохраняется как `skipped` и отображается на обзоре с причиной. Сегодняшнее задание пропущено согласно сохранённой настройке `overdue_minutes=0`.

Текущие проблемы показываются отдельно от истории: 13 предупреждений о неполных строках и 1 ошибка `Шаблон.xlsx` вне папки года. Повтор одинаковой сверки не добавляет их снова. Подписи полей вынесены над полями с переносом текста; высота списков исправлена.

Обновлён только `nc_app_exapp_events` через AppAPI. Выполнен SQLite online backup `backups/pre-0.1.2-20261006T111048Z.sqlite3` в persistent volume; настройки сохранились. Все 21 остальные контейнеры сохранили ID/StartedAt. API и JS/CSS отвечают 200, контейнер healthy, диагностика AppAPI/listeners/storage/SQLite/источника/Calendar/SMTP успешна. 108 backend-тестов, 8 frontend-тестов, Ruff, TypeScript, ESLint, сборка UI/Docker и [CI для кода релиза](https://github.com/kyasu-404/exapp-events/actions/runs/37454655776) прошли. Контрольное письмо не отправлялось по выбору пользователя.

## Автоматическая синхронизация и архив 6 октября

На 0.1.3 по запросу пользователя отключено подтверждение массового удаления: `delete_guard=false`. Сохранение этой настройки поставило сверку в фоновую очередь. Приложение создало 85 событий и удалило 39 старых без ошибок CalDAV; повторная сверка показывает 0 созданий, обновлений и удалений. В календаре 126 событий приложения. Старые отчёты, ожидавшие подтверждения, отмечены `superseded`. 13 предупреждений о неполных строках и ошибка шаблона вне папки года остаются; из-за последней полный отчёт имеет состояние `partial`, хотя все операции Calendar выполнены.

В «Источник данных» добавлена необязательная архивная папка. Пока она не выбрана. Папка и её вложения исключаются из обхода и чтения XLSX; переименование отслеживается по ID. Для ранее обработанных файлов проверяется только текущий путь по ID. По выбранному пользователем правилу перенос XLSX в архив удаляет все связанные события, включая прошедшие, из обоих настроенных календарей и отменяет ожидающие напоминания. Недоступный архив останавливает сверку до восстановления доступа. Возврат файла в источник возобновляет синхронизацию. Изменение настроек архива автоматически ставит сверку в очередь.

Обновлён только контейнер `nc_app_exapp_events` через AppAPI. Online backup: `backups/pre-0.1.3-20261006T114107Z.sqlite3` в persistent volume. Все остальные настройки сверены с backup и сохранены. API и JS/CSS отвечают 200, контейнер healthy; AppAPI, listeners, storage, SQLite, источник, Calendar и SMTP проходят диагностику. Все 21 остальные работающие контейнеры сохранили ID/StartedAt; Nextcloud остаётся вне maintenance. 127 backend-тестов, 8 frontend-тестов, Ruff, TypeScript, ESLint, UI/Docker build и [CI кода релиза](https://github.com/kyasu-404/exapp-events/actions/runs/37458028653) прошли. Письма не отправлялись. Лимиты и restart policy сохранены, отдельный builder остановлен после сборки.

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
