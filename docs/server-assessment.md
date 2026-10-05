# Обследование Nextcloud — 5 октября 2026

Доступ: `ssh stss`, окружение `/opt/nextcloud/`. Выполнены только команды чтения: версии, список приложений/daemon, отдельные поля Docker, просмотр установленного PHP-кода, права файлов сертификатов и `occ --help`. Не запускались регистрация ExApp, обновление контейнеров, создание календарей, изменения настроек или отправка SMTP. Секретные значения в отчёт не включены.

## Установленное окружение

| Компонент | Обнаружено |
| --- | --- |
| Nextcloud | 34.0.2 |
| AppAPI | 34.0.0 |
| Calendar | 6.6.1 |
| Webhook Listeners | 1.6.0, включён |
| ONLYOFFICE connector | 10.2.1 |
| Nextcloud container | `nextcloud`, работает |
| HaRP container | `nc_harp`, работает |
| HaRP image | `ghcr.io/nextcloud/nextcloud-appapi-harp:release` |
| Default deploy daemon | `harp_proxy_docker`, HaRP Proxy (Docker) |
| Daemon API | `appapi-harp:8780` |
| FRP server | `appapi-harp:8782` |
| Docker network | `nextcloud-net` |
| ExApps | `file_control` 2.3.0, `oo_file_collector` 1.0.5, включены |

В `/opt/nextcloud/exapps/` уже есть каталоги других приложений. Создавать там ещё один самостоятельный compose-стек для `exapp-events` не требуется: будущая установка должна использовать существующий AppAPI daemon.

## Решения по совместимости

**Файловые события.** В установленном AppAPI 34 отсутствует реализация прежнего `EventsListenerController`. Упоминания старых routes и страница документации Events Listener ещё встречаются; перенос примера с этим endpoint не дал бы работающую регистрацию. Использован включённый Webhook Listeners и API `nc_py_api.webhooks`: четыре события `OCP\Files\Events\Node\NodeCreatedEvent`, `NodeWrittenEvent`, `NodeDeletedEvent`, `NodeRenamedEvent`. Callback задаётся относительным `/events/files` и вызывается как доверенный AppAPI ExApp request через HaRP. Публичный обход подписи не нужен.

**ID файлов.** SDK различает DAV ID с instance suffix и числовой node ID. Payload Nextcloud содержит числовой `id`; чтение и очередь сохраняют `node.info.fileid`, чтобы rename/delete callbacks сопоставлялись со стабильным источником.

**HaRP TLS.** `client.key` в `/opt/nextcloud/appapi-harp-certs/frp/` принадлежит root и имеет `0600`. Непривилегированный процесс приложения не мог бы читать такой bind mount напрямую. Entrypoint копирует только `client.crt`, `client.key`, `ca.crt` в приватный tmp-каталог контейнера и передаёт его FRP; файлы хоста и существующего HaRP остаются прежними.

**Calendar.** SDK 0.30.3 предоставляет asynchronous Files/AppAPI API, но не предоставляет равнозначного async Calendar wrapper. Реализован отдельный адаптер стандартных CalDAV `PROPFIND`, `MKCALENDAR`, `REPORT`, `PUT`, `DELETE` через authenticated DAV transport SDK. Обращение к внутреннему `_session.adapter_dav` сосредоточено в одном месте и зафиксировано версией SDK; при его обновлении нужны контрактные тесты. Недокументированные REST endpoints Calendar не используются.

**Выбор папки.** Использован remote picker `@nextcloud/dialogs` с выбором directory; `NcFilePicker` современной `@nextcloud/vue` выбирает локальный файл загрузки и не подходит для существующей папки Nextcloud.

**Права.** Маршруты UI/API декларированы как `ADMIN`, Top Menu — `adminRequired`. Backend повторно проверяет реальную группу admin пользователя через SDK. Legacy API scopes в AppAPI 34 удалены; шаблон не повторяет устаревшие scopes.

## Граница проверки

Окружение подходит для дальнейшей тестовой установки, но успешная установка именно этого образа, реальная доставка callbacks, запись CalDAV и SMTP в нём пока не подтверждены. В локальном рабочем окружении отсутствует Docker CLI/runtime, поэтому контейнер здесь не собирался. Добавлена CI-сборка без push; сам GitHub workflow не запускался, так как проект не публиковался.

Сборка UI и тесты backend выполнены локально. Отдельная приёмка после разрешённой установки описана в [acceptance.md](acceptance.md).

Официальные ориентиры: [Python skeleton](https://github.com/nextcloud/app-skeleton-python), [AppAPI changelog](https://github.com/nextcloud/app_api/blob/main/CHANGELOG.md), [Webhook Listeners](https://docs.nextcloud.com/server/latest/admin_manual/webhook_listeners/index.html), [ExApp routes](https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/api/routes.html). Stable-документация может описывать более новую версию; в спорных местах основанием был установленный код AppAPI 34.
