# exapp-events — техническое задание

## 1. Назначение

Разработать Nextcloud ExApp для автоматической синхронизации мероприятий из Excel-файлов `.xlsx`, хранящихся в Nextcloud и редактируемых через ONLYOFFICE, с Nextcloud Calendar.

**Имя проекта / репозитория:** `exapp-events`  
**Nextcloud App ID:** `exapp_events`  
**Отображаемое имя в Nextcloud:** **«Мероприятия»**

> Дефис нельзя использовать в Nextcloud App ID: он должен состоять из строчных ASCII-букв, цифр и `_`. Поэтому имя проекта остаётся `exapp-events`, а внутренний App ID — `exapp_events`.

Excel является **единственным источником истины**. События календаря генерируются и обновляются из Excel; обратная синхронизация Calendar → Excel не выполняется.

Приложение должно:
- отслеживать Excel-файлы в выбранной папке Nextcloud, включая вложенные папки;
- учитывать структуру «год → месяц → Excel-файл», а дату мероприятия определять по вкладке Excel;
- создавать, обновлять и удалять события во внутреннем календаре;
- при необходимости вести отдельный публичный календарь для сайта;
- выборочно отправлять email-напоминания на **произвольные адреса**, указанные непосредственно в Excel;
- иметь полноценный административный UI внутри Nextcloud;
- быть доступным для управления **только администраторам Nextcloud**;
- разворачиваться как ExApp через **AppAPI + HaRP**;
- хранить состояние в persistent storage ExApp;
- переживать перезапуск и обновление контейнера без потери данных.

## 2. Базовые принципы

1. Excel — источник истины.
2. Calendar — производное представление.
3. Основная синхронизация — **event-driven** через AppAPI Events Listener.
4. Периодическая полная сверка — страховка от пропущенных событий и простоя.
5. Синхронизация должна быть **идемпотентной**: повторная обработка одного состояния не создаёт дубликаты.
6. Ошибки одного компонента не должны ломать остальные: SMTP, Calendar и обработка XLSX работают независимо.
7. Не использовать недокументированные Nextcloud API без необходимости.
8. Перед реализацией проверять актуальные возможности установленной версии Nextcloud/AppAPI и `nc_py_api`.
9. UI должен использовать `@nextcloud/vue` и выглядеть как нативная часть Nextcloud.

## 3. Архитектура

```text
                        Nextcloud
                            │
                      AppAPI + HaRP
                            │
                    ┌───────▼────────┐
                    │  exapp_events  │
                    │     ExApp      │
                    └───────┬────────┘
                            │
         ┌──────────────────┼────────────────────┐
         │                  │                    │
         ▼                  ▼                    ▼
  Files / XLSX        Internal Calendar     Public Calendar
         │                  │                    │
         │                  │                    └──► сайт
         │                  │
         └──────────────► SMTP queue
                           │
                           └──► произвольные email
```

Backend:
- Python;
- FastAPI;
- `nc_py_api` там, где он предоставляет нужные возможности;
- `openpyxl`;
- SQLite;
- scheduler для фоновых задач.

Frontend:
- Vue;
- TypeScript;
- `@nextcloud/vue`.

Служебные данные хранить в `APP_PERSISTENT_STORAGE`.

## 4. AppAPI / lifecycle

Использовать стандартные переменные окружения ExApp: `APP_ID`, `APP_SECRET`, `APP_DISPLAY_NAME`, `APP_VERSION`, `APP_HOST`, `APP_PORT`, `NEXTCLOUD_URL`, `APP_PERSISTENT_STORAGE`.

Реализовать стандартный lifecycle:
- `GET /heartbeat`;
- `POST /init`;
- `PUT /enabled`.

При включении:
- проверить/зарегистрировать Events Listener;
- зарегистрировать Top Menu;
- выполнить миграции SQLite;
- восстановить незавершённую очередь email;
- запустить scheduler;
- при включённой опции выполнить контрольную полную сверку.

При отключении:
- корректно остановить workers;
- не удалять БД, настройки и состояние.

## 5. Доступ и безопасность

UI доступен **только администраторам Nextcloud**.

Top Menu:
- `displayName = Мероприятия`;
- `adminRequired = 1`.

Все пользовательские API-маршруты ExApp, вызываемые UI, объявлять с `access_level = ADMIN`.

Обычный пользователь:
- не видит пункт «Мероприятия»;
- при прямом вызове административного API получает `403`;
- не может читать настройки, журнал, SMTP-конфигурацию и состояние синхронизации.

Lifecycle/event callback routes защищать штатным AppAPIAuth и не ломать искусственным `ADMIN`-ограничением.

SMTP-пароль и иные секреты хранить через AppConfig как sensitive values; не возвращать пароль в UI после сохранения и не логировать его.

## 6. Интерфейс ExApp

Левая навигация:

```text
Мероприятия

• Обзор
• Источник данных
• Поля Excel
• Календари
• Уведомления
• Журнал
```

### 6.1. Обзор

Показывать:
- состояние приложения;
- выбранную папку-источник;
- количество отслеживаемых XLSX;
- количество событий во внутреннем календаре;
- количество публичных событий;
- количество ожидающих/неудачных email;
- время последнего Files event;
- время последней полной сверки;
- время последней успешной синхронизации;
- число предупреждений и ошибок.

Кнопки:

**«Синхронизировать сейчас»** — полная фактическая сверка.

**«Проверить без изменений»** — dry-run, который ничего не меняет и показывает:
- сколько событий будет создано;
- сколько обновлено;
- сколько удалено;
- сколько public events изменится;
- сколько email jobs будет поставлено в очередь;
- предупреждения и ошибки.

**«Повторить ошибки»** — повтор retryable Calendar/SMTP операций.

Диагностика:

```text
AppAPI                  ✓
Events Listener         ✓
Persistent storage      ✓
SQLite                  ✓
Папка-источник          ✓
Calendar API            ✓
SMTP                    ✓ / не настроен
```

Кнопка «Проверить конфигурацию» должна проверить источник, SQLite, listener, календарь и SMTP.

## 7. Источник данных

### 7.1. Выбор папки

Поле:

```text
Папка с мероприятиями
[ /Общие/Мероприятия                    ] [Выбрать]
```

Использовать `NcFilePicker` с выбором **папки**.

Сохранять по возможности:
- owner/user context;
- стабильный node/file ID;
- актуальный path.

Не полагаться только на строковый путь, если API даёт стабильный идентификатор.

ExApp **никогда не изменяет исходные XLSX**.

### 7.2. Настройки источника

```text
[✓] Искать XLSX во вложенных папках

Маска файлов
[ *.xlsx ]

Исключать
[ ~$* ]

Основной механизм
● События Nextcloud

Задержка после изменения файла
[ 10 ] секунд

Контрольная полная сверка
[ 30 минут ▼ ]

[✓] Выполнять полную сверку после запуска ExApp
```

Варианты контрольной сверки:
- выключено;
- 5 минут;
- 15 минут;
- 30 минут;
- 1 час;
- 6 часов;
- 24 часа.

По умолчанию: **30 минут**.

Основной механизм — AppAPI Events Listener. Подписаться минимум на:
- `NodeCreatedEvent`;
- `NodeWrittenEvent`;
- `NodeDeletedEvent`;
- `NodeRenamedEvent`.

Допустимо обрабатывать `NodeCopiedEvent` и `NodeTouchedEvent`, если это реально нужно.

Сделать debounce: несколько событий одного file/node ID за короткое окно приводят к одной обработке.

## 8. Структура папок и дата

Код не должен быть привязан к фиксированному количеству уровней.

Пример:

```text
/Мероприятия/
    /2026/
        /Сентябрь/
            Сетка по аудиториям. Сентябрь.xlsx
        /Октябрь/
            Сетка по аудиториям. Октябрь.xlsx
    /2027/
        /Январь/
            Сетка по аудиториям. Январь.xlsx
```

Год определять по ближайшему родительскому каталогу, соответствующему:

```regex
^(19|20)\d{2}$
```

Если год определить невозможно — не создавать события, записать ошибку и показать её в UI.

Названия листов поддержать в форматах:
- `D.M`;
- `D.MM`;
- `DD.M`;
- `DD.MM`;
- те же варианты с точкой в конце;
- пробелы по краям игнорировать.

Пример:

```text
/2026/Сентябрь/Сетка.xlsx
лист: 21.09
→ 21.09.2026
```

Настройка:

```text
[✓] Проверять соответствие месяца листа папке месяца
```

По умолчанию включена. Поддержать русские названия месяцев без учёта регистра. Если файл находится в `/Сентябрь/`, а лист называется `15.10`, создать Warning и по умолчанию **не синхронизировать такой лист**.

## 9. Экран «Поля Excel»

Не использовать жёсткие номера колонок A/B/C. Заголовки искать по тексту.

| Поле приложения | Заголовок Excel по умолчанию | Обязательное |
|---|---|---|
| Место | `Аудитория` | да |
| Время | `Время` | да |
| Название | `Мероприятие` | да |
| Ответственный | `Ответственный` | да |
| Email для уведомления | `Email для уведомления` | нет |
| Напомнить за | `Напомнить за` | нет |
| Публиковать на сайте | `На сайт` | нет |

UI должен позволять изменить имя каждого заголовка.

Добавить кнопку **«Проверить по существующему файлу»**: выбрать XLSX, показать найденные листы, год, заголовки, первые 10 распознанных мероприятий и предупреждения, ничего не записывая в Calendar.

**Не реализовывать сопоставление ФИО с Nextcloud Users.**

`Ответственный` — произвольный текст: сотрудник ИМЦ, школа, внешний специалист, организация, несколько ФИО и т. п.

Email задаётся отдельно в Excel и никак не вычисляется из ФИО.

## 10. Email-поля в Excel

### Email для уведомления

Поддержать один и несколько адресов. Разделители:
- `;`;
- `,`;
- перевод строки.

Адреса trim, валидировать и дедуплицировать.

Если один адрес некорректный:
- событие Calendar всё равно синхронизируется;
- корректные адреса используются;
- некорректный адрес создаёт Warning.

Если поле пустое — email не создаются.

### Напомнить за

Поддержать:

```text
30м
1ч
2ч
6ч
12ч
24ч
48ч
1д
2д
```

и английские эквиваленты `30m`, `1h`, `2h`, `1d`, `2d`.

Несколько интервалов:

```text
24ч;2ч
```

Некорректный интервал не мешает Calendar sync, но даёт Warning.

Отдельный столбец «Уведомлять» не нужен: есть email + валидный offset → уведомление включено.

## 11. Чтение XLSX

Использовать `openpyxl`.

Поддержать значения времени:
- Excel native time;
- Excel datetime;
- строка `15:30`;
- строка `15.30`;
- `15:30-17:00`;
- `15:30 – 17:00`;
- `15.30–17.00`.

Пустые строки пропускать. Форматирование Excel не считать данными.

Скрытые строки в MVP **обрабатывать**.

Если ячейка содержит формулу, использовать cached value, если он есть. Если обязательное значение получить нельзя — Warning/Error для строки.

## 12. Продолжительность события

Если окончание указано в поле времени — использовать его.

Если указано только начало:

```text
DTEND = DTSTART + default_duration
```

Настройка:

```text
Продолжительность по умолчанию
[ 60 ] минут
```

Диапазон: 5–1440 минут.

## 13. Формирование внутреннего события

Для корректной строки:

```text
SUMMARY     = Мероприятие
LOCATION    = Аудитория
DESCRIPTION = Ответственный: <текст>
DTSTART     = дата листа + время начала
DTEND       = окончание или default duration
```

Email-адреса:
- не добавлять в `DESCRIPTION`;
- не добавлять в public Calendar;
- не добавлять в `ATTENDEE`;
- не использовать Calendar invitations ради email-рассылки.

## 14. Идентификация событий

Для MVP строить source key из:

```text
source_file_id
sheet_date
start_time
normalized_location
```

На его основе использовать детерминированный UID, например UUIDv5.

Ожидаемое поведение:
- изменение названия → UPDATE;
- изменение ответственного → UPDATE;
- перестановка строк → ничего не ломает;
- изменение места/времени → старое событие удаляется, новое создаётся.

Если две строки имеют одинаковые дату + время начала + место, считать это конфликтом: не объединять молча и показать Error/Conflict.

В VEVENT добавить служебные свойства:

```text
X-EXAPP-EVENTS:1
X-EXAPP-EVENTS-SOURCE-FILE-ID:<id>
X-EXAPP-EVENTS-SOURCE-SHEET:<sheet>
X-EXAPP-EVENTS-SOURCE-KEY:<key>
```

Они нужны для восстановления связи при повреждении SQLite.

## 15. Алгоритм синхронизации

```text
получить актуальный XLSX
        ↓
разобрать подходящие листы
        ↓
построить Desired State
        ↓
получить Current State
        ↓
diff
  ├─ CREATE
  ├─ UPDATE
  └─ DELETE
        ↓
применить изменения
        ↓
обновить SQLite
```

Требования:
- идемпотентность;
- mutex/lock на один `file_id`;
- разные файлы можно обрабатывать независимо;
- хранить hash нормализованных данных;
- если содержимое не изменилось, Calendar не переписывать.

Для файла хранить минимум:
- `file_id`;
- path;
- etag/mtime при наличии;
- content hash;
- last_seen;
- last_sync_at;
- status;
- last_error.

## 16. Переименование, перемещение и удаление XLSX

### Переименование/перемещение
Если node/file ID прежний:
- сохранить связь с событиями;
- обновить path;
- не удалять и не пересоздавать всё без необходимости.

### Удаление источника
Настройка:

```text
При исчезновении XLSX:
● Удалять только будущие связанные события
○ Удалять все связанные события
○ Не удалять события автоматически
```

По умолчанию — **удалять только будущие связанные события**.

### Удаление строки внутри существующего XLSX
Если строка исчезла из действующего файла:
- удалить соответствующее будущее событие;
- прошедшее событие по умолчанию оставить в истории.

## 17. Экран «Календари»

### Внутренний календарь

```text
Владелец календаря
[ пользователь Nextcloud ▼ ]

Внутренний календарь
[ Мероприятия ▼ ]

[ Создать новый календарь ]
```

Если актуальный официальный API позволяет создание календаря — реализовать.

Если надёжного API нет:
- не придумывать endpoint;
- позволить выбрать существующий Calendar;
- показать инструкцию создать календарь штатно.

Права общего доступа сотрудникам в MVP можно оставить штатному интерфейсу Nextcloud Calendar.

Excel остаётся источником истины. Если пользователь вручную изменил сгенерированное событие Calendar, следующая reconciliation должна вернуть его к состоянию Excel. Если вручную удалил — создать заново.

## 18. Публичный календарь

Настройка:

```text
[✓] Формировать публичный календарь

Публичный календарь
[ Мероприятия сайта ▼ ]

[ Создать новый календарь ]
```

В него попадают только строки, где `На сайт` распознано как true.

True-значения:

```text
Да
да
yes
true
1
+
```

False-значения:

```text
Нет
нет
no
false
0
-
<пусто>
```

Публичное событие содержит только:
- название;
- дату;
- время;
- место.

Не переносить:
- ответственного;
- email;
- reminder offsets;
- служебные данные Excel.

Если `На сайт` меняется `Да → Нет`, удалить только public event, internal оставить.

## 19. Встраивание на сайт

В разделе «Календари» добавить блок **«Публикация на сайте»**.

Для MVP допустимо создавать public share вручную в штатном Calendar, если стабильного API автоматического создания нет.

UI:

```text
Публичная ссылка Calendar
[ https://cloud.example/... ]

Режим отображения
[ Список месяца ▼ ]

Дата
● Текущая
○ Конкретная

[ Скопировать iframe ]
```

Поддержать режимы Nextcloud Calendar:
- `dayGridMonth`;
- `timeGridWeek`;
- `timeGridDay`;
- `listMonth`;
- `listWeek`;
- `listDay`.

Формировать embed URL:

```text
https://cloud.example/index.php/apps/calendar/embed/<token>/<view>/now
```

и готовый iframe.

## 20. Экран «Уведомления»

Email должен работать для получателей без аккаунта Nextcloud.

Использовать прямой SMTP.

```text
[✓] Email-уведомления

SMTP-сервер
[ smtp.example.org ]

Порт
[ 587 ]

Шифрование
[ STARTTLS ▼ ]

Логин
[ ................................ ]

Пароль
[ ••••••••••••••• ]

Email отправителя
[ events@example.org ]

Имя отправителя
[ Мероприятия ]

[ Сохранить ]

Тестовый получатель
[ test@example.org ]

[ Отправить тестовое письмо ]
```

Варианты шифрования:
- STARTTLS;
- SSL/TLS;
- без шифрования — только при явном выборе с предупреждением.

Пароль хранить как sensitive AppConfig. После сохранения не возвращать значение в браузер.

## 21. Очередь email

Reminder job должен хранить минимум:

```text
event_uid
recipient
offset_seconds
scheduled_at
status
attempt_count
next_attempt_at
last_error
event_revision
dedupe_key
created_at
sent_at
```

Статусы:
- `pending`;
- `sending`;
- `sent`;
- `retry`;
- `failed`;
- `cancelled`.

Уникальность — `event_uid + recipient + offset + event_revision` или эквивалентная детерминированная схема.

Требования:
- повторный Files event не создаёт дубликаты писем;
- restart не вызывает повторную отправку `sent`;
- pending/retry восстанавливаются после restart;
- изменение события пересчитывает ещё не отправленные jobs;
- просроченные уведомления по умолчанию не отправляются задним числом.

Настройка:

```text
Если ExApp был выключен:
● Не отправлять просроченные напоминания
○ Отправлять, если просрочка не более [ 30 ] минут
```

По умолчанию — не отправлять просроченные.

## 22. Повтор SMTP-ошибок

Пример backoff:
- 1 минута;
- 5 минут;
- 15 минут;
- 1 час.

После максимума попыток:
- `failed`;
- запись в журнал;
- счётчик на Overview;
- возможность ручного retry.

SMTP не должен блокировать Calendar sync.

## 23. Формат письма

Тема:

```text
Напоминание: {название} — {дата} {время}
```

Текст:

```text
Напоминаем о мероприятии:

{название}

Дата: 21.09.2026
Время: 15:30
Место: 9 кабинет
Ответственный: Максимова Н.А., Ивашкевич А.Г.

Это автоматическое уведомление.
```

Отправлять HTML + plain text fallback.

## 24. Изменение/отмена после отправленного напоминания

Настройки:

```text
[✓] Уведомлять об изменении мероприятия, если ранее уже было отправлено письмо
[✓] Уведомлять об отмене мероприятия, если ранее уже было отправлено письмо
```

Если уже было хотя бы одно `sent` письмо конкретному адресу и изменились дата, время или место — отправить отдельное письмо об изменении.

Если будущее мероприятие удалено — отправить «Отменено мероприятие» только тем адресам, которым ранее реально отправлялось уведомление.

Изменение только названия или поля «Ответственный» обновляет Calendar, но change-email по умолчанию не отправляет.

## 25. Версионирование события

Каждое нормализованное событие имеет `revision`, увеличивающийся при значимом изменении.

Значимые поля:
- date;
- start/end;
- location;
- title;
- responsible;
- public flag;
- recipients;
- reminder offsets.

Revision используется для dedupe, отмены устаревших pending jobs и change notifications.

При изменении времени события:
- pending/retry reminders старой revision отменить;
- рассчитать новые `scheduled_at`;
- создать jobs новой revision;
- уже `sent` не трогать.

## 26. Журнал

Экран **«Журнал»**.

Таблица:

```text
Дата/время | Уровень | Подсистема | Файл | Лист | Операция | Результат
```

Уровни:
- Info;
- Warning;
- Error.

Подсистемы:
- Events;
- Excel;
- Sync;
- Calendar;
- Email;
- AppAPI;
- Scheduler.

Фильтры:
- период;
- уровень;
- подсистема;
- файл;
- только ошибки.

Кнопки:
- «Скачать журнал»;
- «Очистить журнал».

Retention:

```text
Хранить журнал
[ 90 дней ▼ ]
```

Варианты: 30, 90, 180, 365 дней. По умолчанию 90.

Не логировать SMTP password, AppAPI secret, Authorization headers.

## 27. SQLite

Минимальные таблицы:

```text
schema_migrations
source_files
source_events
calendar_events
reminders
sync_runs
logs
```

### source_files

```text
file_id
path
etag
content_hash
last_seen_at
last_sync_at
status
last_error
```

### source_events

```text
source_key
file_id
sheet
event_date
start_time
end_time
location
title
responsible
data_hash
internal_uid
public_uid
public_enabled
revision
created_at
updated_at
last_seen_at
```

### reminders

```text
id
event_uid
recipient
offset_seconds
scheduled_at
sent_at
status
attempt_count
next_attempt_at
dedupe_key
event_revision
last_error
created_at
updated_at
```

### sync_runs

```text
id
type
started_at
finished_at
status
files_scanned
events_created
events_updated
events_deleted
warnings
errors
```

Использовать versioned schema migrations.

## 28. Работа с Calendar API

Предпочтительный порядок:
1. возможности `nc_py_api`;
2. поддерживаемые AppAPI/OCS APIs;
3. документированный DAV/CalDAV;
4. только при отсутствии альтернатив — минимальный адаптер к подтверждённому API текущей версии.

Не придумывать endpoints.

AppAPI permissions в `info.xml` должны быть минимально необходимыми: Calendar, File System и, если требуется для выбора владельца календаря, Users & Groups.

## 29. Работа с Files

ExApp должен:
- получать Files event;
- проверять, принадлежит ли node выбранному source tree;
- получать актуальное содержимое XLSX через поддерживаемый API;
- не записывать обратно в файл.

Если event payload не содержит всех нужных данных — использовать file/node ID и дозапрашивать metadata.

При полной сверке:
- рекурсивно перечислить `.xlsx`;
- применить include/exclude mask;
- сравнить с SQLite;
- обработать изменения/исчезновения.

## 30. Конкурентность

Для одного `file_id` должен быть mutex/lock.

Несколько последовательных `NodeWrittenEvent` одного файла объединять через debounce/coalescing.

Разные XLSX можно обрабатывать параллельно с ограничением concurrency, например 2–4 файла.

## 31. Поведение при ошибочном XLSX

Если файл повреждён, временно недописан ONLYOFFICE или не читается:
- **не считать его пустым**;
- не удалять ранее существовавшие события этого файла;
- записать Error;
- повторить обработку после задержки;
- поздняя reconciliation должна исправить состояние.

Это обязательная защита от массового удаления из-за временной ошибки чтения.

## 32. ONLYOFFICE

ExApp не должен обращаться напрямую к ONLYOFFICE Document Server.

Он реагирует только на фактическое изменение файла в Nextcloud.

В README указать, что для быстрой синхронизации желательно включить промежуточное сохранение/forcesave в коннекторе ONLYOFFICE. Если файл реально записывается в Nextcloud только после закрытия редактора, ExApp не может увидеть изменения раньше.

## 33. Timezone

UI:

```text
Часовой пояс мероприятий
[ Europe/Moscow ▼ ]
```

По умолчанию использовать timezone экземпляра Nextcloud, если доступен; иначе администратор выбирает вручную.

Хранить timezone-aware datetime. Не использовать timezone контейнера как источник истины.

## 34. Защита от массовых destructive changes

Если одна синхронизация собирается удалить аномально много событий, например более 25% событий файла или более 50 событий, не выполнять автоматическое удаление сразу.

Перевести sync в `requires_confirmation` и показать:

```text
Обнаружено массовое удаление: 73 события.

[Просмотреть]
[Подтвердить удаление]
[Отменить]
```

Настройки:

```text
[✓] Защита от массового удаления
Порог: [ 25 ] %
Минимум: [ 20 ] событий
```

## 35. Admin API

Нужен внутренний REST API минимум для:

```text
GET    /api/status
GET    /api/settings
PUT    /api/settings
POST   /api/sync
POST   /api/dry-run
POST   /api/diagnostics
GET    /api/files
POST   /api/files/preview
GET    /api/calendars
POST   /api/calendars
POST   /api/smtp/test
GET    /api/logs
DELETE /api/logs
POST   /api/retry-failed
```

Точные URL можно корректировать, но все административные маршруты должны быть `ADMIN`, с backend validation и понятными JSON errors.

## 36. Frontend UX

Использовать компоненты `@nextcloud/vue`, в том числе по необходимости:
- `NcAppNavigation`;
- `NcButton`;
- `NcTextField`;
- `NcSelect`;
- `NcCheckboxRadioSwitch`;
- `NcSettingsSection`;
- `NcModal`;
- `NcFilePicker`;
- `NcLoadingIcon`;
- стандартные notifications/toasts.

Для длительных операций показывать progress. Backend может возвращать run/job ID, UI опрашивает состояние.

## 37. Dry-run

Dry-run обязателен.

Он:
- читает данные;
- выполняет парсинг;
- строит diff;
- не меняет internal/public Calendar;
- не создаёт реальные reminder jobs;
- не отправляет SMTP.

Результат содержит файл, лист, source key, тип операции и причину.

## 38. Public/internal при ошибках

Internal, public и SMTP состояния разделить.

Если public Calendar недоступен:
- internal sync всё равно завершается;
- public operation остаётся retryable.

Если SMTP недоступен:
- Calendar sync всё равно завершается;
- email уходит в retry.

## 39. Что не делать в MVP

Не включать:
- Calendar → Excel;
- собственный редактор мероприятий;
- сопоставление ФИО с пользователями Nextcloud;
- Contacts integration;
- attendees/invitations;
- роли кроме Admin;
- автоматическое управление shares внутреннего календаря;
- Nextcloud bell notifications;
- workflow согласования;
- запись обратно в XLSX;
- прямую интеграцию с ONLYOFFICE Document Server;
- автоматическое создание public share через недокументированный endpoint.

## 40. Рекомендуемая структура проекта

Ориентироваться на актуальный официальный ExApp skeleton.

```text
exapp-events/
├── appinfo/
│   └── info.xml
├── ex_app/
│   ├── lib/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── services/
│   │   │   ├── nextcloud_files.py
│   │   │   ├── excel_parser.py
│   │   │   ├── sync_engine.py
│   │   │   ├── calendar_service.py
│   │   │   ├── reminder_service.py
│   │   │   ├── smtp_service.py
│   │   │   ├── event_listener.py
│   │   │   └── scheduler.py
│   │   ├── db/
│   │   │   ├── models.py
│   │   │   └── migrations/
│   │   └── tests/
│   └── src/
│       ├── App.vue
│       ├── main.ts
│       ├── views/
│       │   ├── Overview.vue
│       │   ├── Source.vue
│       │   ├── ExcelFields.vue
│       │   ├── Calendars.vue
│       │   ├── Notifications.vue
│       │   └── Logs.vue
│       └── components/
├── l10n/
├── Dockerfile
├── pyproject.toml
├── package.json
├── README.md
└── ...
```

Структуру адаптировать под актуальный skeleton, если он отличается.

## 41. App metadata

```text
Project/repository: exapp-events
Nextcloud app id:   exapp_events
Display name:       Мероприятия
Initial version:    0.1.0
```

Использовать semantic versioning.

## 42. Тесты backend

Обязательные unit tests:

### Даты
- `21.09` + `/2026/` → `2026-09-21`;
- `1.10`;
- `01.10.`;
- неизвестный год;
- месяц листа не соответствует папке.

### Время
- Excel time;
- Excel datetime;
- `15:30`;
- `15.30`;
- `15:30-17:00`;
- `15:30 – 17:00`;
- invalid time.

### Excel
- пустые строки;
- изменённый порядок колонок;
- отсутствует обязательный header;
- формула без cached value;
- повреждённый XLSX.

### Sync
- add row;
- update title;
- update responsible;
- update time;
- update location;
- delete row;
- reorder rows;
- duplicate date+time+location;
- repeat identical sync;
- concurrent duplicate events;
- rename/move/delete source file.

### Public
- `Да` → create;
- `Да → Нет` → delete public only;
- `Нет → Да` → create;
- public event не содержит responsible/email.

### Email
- single/multiple address;
- invalid/duplicate address;
- `24ч;2ч`;
- duplicate reminder suppression;
- restart with pending queue;
- retry SMTP failure;
- reschedule on time change;
- cancellation after prior sent reminder.

### Security
- admin → доступ;
- normal user → 403;
- secret not returned;
- secret not logged.

## 43. Integration tests

По возможности поднять тестовый Nextcloud + AppAPI/ExApp environment и проверить:
1. ExApp устанавливается через AppAPI/HaRP.
2. Lifecycle работает.
3. Top Menu виден только admin.
4. Events Listener получает `NodeWrittenEvent`.
5. XLSX читается.
6. VEVENT создаётся.
7. Повторное событие не создаёт дубликат.
8. Изменение XLSX обновляет Calendar.
9. Public Calendar обновляется независимо.
10. SMTP queue переживает restart.
11. Full reconciliation исправляет пропущенное изменение.

## 44. Порядок разработки

Не реализовывать всё одним коммитом.

### Этап 1 — каркас
- актуальный ExApp skeleton;
- `info.xml`;
- AppAPI/HaRP deploy;
- lifecycle;
- admin-only Top Menu;
- Vue shell;
- SQLite + migrations.

### Этап 2 — источник
- выбор папки;
- Files API;
- Events Listener;
- debounce;
- recursive reconciliation;
- список найденных XLSX.

### Этап 3 — Excel
- `openpyxl`;
- определение года/листа;
- mapping headers;
- preview;
- validation;
- normalized event model.

### Этап 4 — internal Calendar
- Calendar adapter;
- create/update/delete;
- UID/source metadata;
- dry-run;
- recovery/reconciliation.

### Этап 5 — public Calendar
- `На сайт`;
- separate public state;
- embed helper.

### Этап 6 — SMTP
- settings;
- sensitive password;
- persistent queue;
- retries;
- scheduler;
- change/cancellation emails.

### Этап 7 — эксплуатация
- Overview;
- diagnostics;
- journal;
- mass-delete protection;
- integration tests;
- README.

На каждом этапе: тесты, lint/type checks, отдельный логически законченный commit.

## 45. Инструкции Codex перед началом

1. Проверить актуальную документацию целевой версии Nextcloud и AppAPI.
2. Проверить актуальный официальный ExApp skeleton.
3. Проверить текущий API `nc_py_api`.
4. Не переносить старые примеры API без проверки.
5. Не придумывать endpoints.
6. Если операция недоступна через стабильный API — использовать документированный DAV/CalDAV, если возможно; иначе оставить ручную операцию администратору и объяснить это в UI.
7. Не менять исходные XLSX.
8. Не реализовывать ФИО → email/user mapping.
9. Не использовать Calendar attendees для нашей рассылки.
10. Соблюдать admin-only доступ ко всему UI.

## 46. Критерии готовности MVP

MVP готов, если администратор может:
1. установить `exapp_events` через AppAPI/HaRP;
2. открыть приложение **«Мероприятия»**;
3. выбрать папку с месячными XLSX;
4. настроить сопоставление заголовков;
5. выбрать внутренний Calendar;
6. при необходимости выбрать public Calendar;
7. выполнить preview XLSX;
8. выполнить dry-run;
9. запустить синхронизацию;
10. увидеть корректные события Calendar;
11. изменить XLSX через ONLYOFFICE и получить автоматическое обновление после фактического сохранения;
12. удалить будущую строку и получить удаление события;
13. указать `На сайт = Да` и получить public event без ФИО/email;
14. указать произвольный внешний email + offset и получить письмо;
15. перезапустить ExApp без потери состояния и очереди;
16. увидеть ошибки и предупреждения в журнале;
17. убедиться, что обычный пользователь UI не видит и административный API не вызывает.

## 47. Модель данных для сотрудников

Процесс должен оставаться максимально близким к текущему: сотрудники работают с обычным XLSX через ONLYOFFICE.

Минимальные колонки:

```text
Аудитория
Время
Мероприятие
Ответственный
...
Email для уведомления
Напомнить за
На сайт
```

Пример:

| Аудитория | Время | Мероприятие | Ответственный | Email для уведомления | Напомнить за | На сайт |
|---|---|---|---|---|---|---|
| 9 кабинет | 15:30 | КПК «...» | Иванова И.И. | school@example.org | 24ч;2ч | Да |
| 10 кабинет | 16:00 | Совещание | Петров П.П. |  |  | Нет |

Первая строка:
- internal Calendar: создаётся;
- public Calendar: создаётся;
- public event не содержит ФИО/email;
- `school@example.org` получает письма за 24 часа и за 2 часа.

Вторая строка:
- internal Calendar: создаётся;
- public Calendar: не создаётся;
- email: не отправляется.

## 48. Definition of Done

Работа завершена только если:
- проект собирается с нуля по README;
- ExApp разворачивается через AppAPI + HaRP;
- UI называется **«Мероприятия»**;
- UI виден только администраторам;
- source folder выбирается через Nextcloud UI;
- Files events обрабатываются;
- full reconciliation работает;
- Excel читается без изменения исходного файла;
- Calendar sync идемпотентен;
- public Calendar отделён от internal;
- произвольные внешние email поддерживаются;
- SMTP queue persistent;
- secrets защищены;
- destructive operations имеют защиту;
- тесты проходят;
- код не содержит захардкоженных URL/паролей/путей конкретного сервера;
- README достаточен для установки, первичной настройки, диагностики и обновления.

## 49. Официальные источники

Перед реализацией перепроверять текущую stable-документацию:

- ExApp overview:  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/development_overview/ExAppOverview.html

- ExApp deployment / environment / lifecycle:  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/Deployment.html

- Events Listener:  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/api/events_listener.html

- ExApp routes / access levels:  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/api/routes.html

- Top Menu / `adminRequired`:  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/api/topmenu.html

- AppConfig / sensitive values:  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/api/appconfig.html

- AppAPI APIs:  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/api/index.html

- Other supported OCS APIs (Calendar, File System, Users & Groups и др.):  
  https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/tech_details/api/other_ocs.html

- Nextcloud UI components / file picker:  
  https://docs.nextcloud.com/server/stable/developer_manual/design/components.html

- Calendar public sharing / embed:  
  https://docs.nextcloud.com/server/stable/user_manual/en/groupware/calendar.html

- App ID naming rules:  
  https://docs.nextcloud.com/server/stable/developer_manual/getting_started/glossary.html
