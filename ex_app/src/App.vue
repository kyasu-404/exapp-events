<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import NcContent from '@nextcloud/vue/components/NcContent'
import NcAppNavigation from '@nextcloud/vue/components/NcAppNavigation'
import NcAppNavigationItem from '@nextcloud/vue/components/NcAppNavigationItem'
import NcAppContent from '@nextcloud/vue/components/NcAppContent'
import NcButton from '@nextcloud/vue/components/NcButton'
import NcCheckboxRadioSwitch from '@nextcloud/vue/components/NcCheckboxRadioSwitch'
import NcSettingsSection from '@nextcloud/vue/components/NcSettingsSection'
import NcModal from '@nextcloud/vue/components/NcModal'
import NcLoadingIcon from '@nextcloud/vue/components/NcLoadingIcon'
import NcSelect from '@nextcloud/vue/components/NcSelect'
import { getFilePickerBuilder, FilePickerType, FilePickerClosed, showError, showSuccess } from '@nextcloud/dialogs'
import { getCurrentUser } from '@nextcloud/auth'
import Field from './components/Field.vue'
import { api, base } from './api'
import { embed, views } from './embed'
import type { Settings, Status, CalendarOption, Run, SourceFile, Log, Preview } from './types'

const pages = ['Обзор', 'Источник данных', 'Поля Excel', 'Календари', 'Уведомления', 'Журнал']
const page = ref('Обзор'), settings = ref<Settings>(), status = ref<Status>(), busy = ref(false), error = ref('')
const users = ref<string[]>([]), calendars = ref<CalendarOption[]>([]), files = ref<SourceFile[]>([]), logs = ref<Log[]>([])
const preview = ref<Preview>(), details = ref<Run>(), diagnostics = ref<{ name: string; status: string; message?: string }[]>([])
const password = ref(''), clearPassword = ref(false), testRecipient = ref(''), newCalendarName = ref('Мероприятия')
const embedView = ref('listMonth'), embedDate = ref(''), logLevel = ref(''), logSubsystem = ref(''), logFile = ref(''), logSince = ref(''), logUntil = ref(''), logOffset = ref(0), clearDialog = ref(false)
const activeRun = computed(() => status.value?.runs.find(r => ['queued', 'running'].includes(r.status)))
const readyCalendars = computed(() => calendars.value.filter(c => c.writable))
const internal = computed({get: () => calendars.value.find(c => c.url === settings.value?.internal_calendar), set: c => { if (settings.value) settings.value.internal_calendar = c?.url ?? '' }})
const publicCalendar = computed({get: () => calendars.value.find(c => c.url === settings.value?.public_calendar), set: c => { if (settings.value) settings.value.public_calendar = c?.url ?? '' }})
const iframe = computed(() => { try { return embed(settings.value?.public_link ?? '', embedView.value, embedDate.value || 'now') } catch (e) { return (e as Error).message } })
const emailCount = (states: string[]) => status.value?.counts.email.filter(r => states.includes(r.status)).reduce((sum, r) => sum + r.n, 0) ?? 0
const date = (value?: string) => value ? new Date(value).toLocaleString('ru-RU', { timeZone: settings.value?.timezone ?? 'Europe/Moscow' }) : '—'
const stateName = (value: string) => ({completed: 'Завершена', partial: 'Есть ошибки', failed: 'Ошибка', requires_confirmation: 'Требует подтверждения', queued: 'В очереди', running: 'Выполняется', dry_run: 'Проверка без изменений', cancelled: 'Отменена', interrupted: 'Прервана', confirmation_queued: 'Подтверждение в очереди'}[value] ?? value)
let timer: ReturnType<typeof setInterval>

async function action(fn: () => Promise<unknown>) {
  busy.value = true; error.value = ''
  try { await fn() } catch (e) { error.value = (e as Error).message; showError(error.value) } finally { busy.value = false }
}
async function refresh() { status.value = await api<Status>('/api/status'); if (details.value) details.value = await api<Run>(`/api/runs/${details.value.id}`) }
async function save() {
  if (!settings.value) return
  const { smtp_password_set: ignored, ...values } = settings.value
  void ignored
  await api('/api/settings', 'PUT', { settings: values, smtp_password: password.value || null, clear_smtp_password: clearPassword.value })
  password.value = ''; clearPassword.value = false; settings.value = await api<Settings>('/api/settings'); showSuccess('Настройки сохранены')
}
async function selectPage(value: string) {
  page.value = value
  await action(async () => {
    if (value === 'Источник данных') files.value = await api('/api/files')
    if (value === 'Календари' && settings.value?.calendar_owner) await loadCalendars()
    if (value === 'Журнал') { logOffset.value = 0; await loadLogs() }
  })
}
async function pickFolder() {
  try {
    const picker = getFilePickerBuilder('Папка с мероприятиями').setMultiSelect(false).allowDirectories()
      .setMimeTypeFilter(['httpd/unix-directory']).setType(FilePickerType.Choose).build()
    const nodes = await picker.pickNodes()
    if (nodes[0] && settings.value) {
      settings.value.source_path = nodes[0].path; settings.value.source_id = String(nodes[0].fileid ?? '')
      if (!settings.value.source_owner) settings.value.source_owner = getCurrentUser()?.uid ?? ''
    }
  } catch (e) { if (!(e instanceof FilePickerClosed)) throw e }
}
async function previewFile() {
  try {
    const picker = getFilePickerBuilder('Проверить XLSX в папке-источнике').setMultiSelect(false)
      .setMimeTypeFilter(['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet']).setType(FilePickerType.Choose)
      .startAt(settings.value?.source_path ?? '/').build()
    const nodes = await picker.pickNodes()
    if (nodes[0]) preview.value = await api('/api/files/preview', 'POST', { file_id: String(nodes[0].fileid) })
  } catch (e) { if (!(e instanceof FilePickerClosed)) throw e }
}
async function run(path: string) { const result = await api<{run_id: string}>(path, 'POST'); details.value = await api(`/api/runs/${result.run_id}`); await refresh() }
async function loadCalendars() { calendars.value = await api(`/api/calendars?owner=${encodeURIComponent(settings.value?.calendar_owner ?? '')}`) }
async function createCalendar() { await api('/api/calendars', 'POST', { owner: settings.value?.calendar_owner, name: newCalendarName.value }); await loadCalendars(); showSuccess('Календарь создан') }
async function copyIframe() { await navigator.clipboard.writeText(iframe.value); showSuccess('iframe скопирован') }
const logQuery = computed(() => new URLSearchParams({level: logLevel.value, subsystem: logSubsystem.value, file: logFile.value, since: logSince.value ? new Date(logSince.value).toISOString() : '', until: logUntil.value ? new Date(logUntil.value).toISOString() : '', offset: String(logOffset.value)}).toString())
async function loadLogs() { logs.value = await api(`/api/logs?${logQuery.value}`) }
async function downloadLogs() {
  const response = await fetch(`${base()}/api/logs?${logQuery.value}&export=true`, {credentials: 'same-origin'})
  if (!response.ok) throw new Error('Не удалось скачать журнал')
  const url = URL.createObjectURL(await response.blob()); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'events-log.csv'; anchor.click(); URL.revokeObjectURL(url)
}
onMounted(() => action(async () => {
  settings.value = await api<Settings>('/api/settings')
  users.value = await api('/api/users')
  if (!settings.value.source_owner) settings.value.source_owner = getCurrentUser()?.uid ?? ''
  if (!settings.value.calendar_owner) settings.value.calendar_owner = getCurrentUser()?.uid ?? ''
  await refresh()
  timer = setInterval(() => refresh().catch(e => { error.value = e.message }), 5000)
}))
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <NcContent app-name="exapp_events">
    <NcAppNavigation aria-label="Разделы приложения">
      <template #list>
        <NcAppNavigationItem v-for="item in pages" :key="item" :name="item" :active="page === item" @click="selectPage(item)" />
      </template>
    </NcAppNavigation>
    <NcAppContent>
      <main class="events-page">
        <header class="page-header"><div><p class="eyebrow">Мероприятия</p><h1>{{ page }}</h1></div><NcLoadingIcon v-if="busy || activeRun" :size="28" /></header>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <p v-if="!settings && !error">Загрузка настроек…</p>

        <template v-if="page === 'Обзор' && status">
          <p class="intro">Excel — источник мероприятий. Изменения после сохранения в Nextcloud обновляют календарь и напоминания.</p>
          <div class="cards">
            <article><span>Приложение</span><strong>{{ status.enabled ? 'Работает' : 'Отключено' }}</strong><small>Версия {{ status.version }}</small></article>
            <article><span>Excel-файлы</span><strong>{{ status.counts.files }}</strong><small>Отслеживается</small></article>
            <article><span>Мероприятия</span><strong>{{ status.counts.events }}</strong><small>В источнике</small></article>
            <article><span>На сайте</span><strong>{{ status.counts.public }}</strong><small>Публичный календарь</small></article>
            <article><span>Письма в очереди</span><strong>{{ emailCount(['pending','retry','sending']) }}</strong><small>Ошибок: {{ emailCount(['failed']) }}</small></article>
            <article><span>Журнал</span><strong>{{ status.counts.logs.filter(l => ['Warning','Error'].includes(l.level)).reduce((n,l) => n+l.n,0) }}</strong><small>Предупреждения и ошибки</small></article>
          </div>
          <dl><dt>Источник</dt><dd>{{ status.source_path || 'Выберите папку в разделе «Источник данных»' }}</dd><dt>Последнее Files event</dt><dd>{{ date(status.last_files_event) }}</dd><dt>Полная сверка</dt><dd>{{ date(status.last_full_reconciliation) }}</dd><dt>Успешная синхронизация</dt><dd>{{ date(status.last_successful_sync) }}</dd></dl>
          <div class="actions">
            <NcButton variant="primary" :disabled="busy || !!activeRun" @click="action(() => run('/api/sync'))">Синхронизировать сейчас</NcButton>
            <NcButton :disabled="busy || !!activeRun" @click="action(() => run('/api/dry-run'))">Проверить без изменений</NcButton>
            <NcButton :disabled="busy || !!activeRun" @click="action(() => run('/api/retry-failed'))">Повторить ошибки</NcButton>
            <NcButton :disabled="busy" @click="action(async () => { diagnostics = await api('/api/diagnostics','POST') })">Проверить конфигурацию</NcButton>
          </div>
          <ul v-if="diagnostics.length" class="diagnostics"><li v-for="item in diagnostics" :key="item.name"><span>{{ item.name }}</span><b :class="{error:item.status==='error'}">{{ item.status === 'ok' ? '✓' : item.status === 'not_configured' ? 'Не настроен' : 'Ошибка' }}</b></li></ul>
          <h2>Последние сверки</h2>
          <p v-if="!status.runs.length">Сверок пока нет. Настройте источник и календарь, затем запустите проверку.</p>
          <div class="table-wrap"><table v-if="status.runs.length"><thead><tr><th>Начало</th><th>Состояние</th><th>Файлы</th><th></th></tr></thead><tbody><tr v-for="item in status.runs" :key="item.id"><td>{{ date(item.started_at) }}</td><td>{{ stateName(item.status) }}</td><td>{{ item.files_scanned }}</td><td><NcButton variant="tertiary" @click="details=item">Просмотреть</NcButton></td></tr></tbody></table></div>
        </template>

        <template v-if="settings && page === 'Источник данных'">
          <NcSettingsSection name="Папка с мероприятиями" description="Файлы читаются в контексте выбранного пользователя. Выбор папки показывает файлы текущего администратора.">
            <NcSelect v-model="settings.source_owner" :options="users" input-label="Владелец / пользователь источника" />
            <div class="actions"><Field v-model="settings.source_path" label="Путь к папке" /><NcButton @click="action(pickFolder)">Выбрать папку</NcButton></div>
            <p v-if="settings.source_id" class="muted">Стабильный ID папки: {{ settings.source_id }}</p>
            <NcCheckboxRadioSwitch v-model="settings.recursive">Искать XLSX во вложенных папках</NcCheckboxRadioSwitch>
            <Field v-model="settings.include" label="Маска файлов" /><Field v-model="settings.exclude" label="Исключать (маски через ;)" />
            <Field v-model="settings.debounce_seconds" label="Задержка после изменения, секунд" numeric />
            <label class="select-label">Контрольная полная сверка<select v-model.number="settings.reconciliation_minutes"><option v-for="n in [0,5,15,30,60,360,1440]" :key="n" :value="n">{{ n ? `${n} минут` : 'Выключена' }}</option></select></label>
            <NcCheckboxRadioSwitch v-model="settings.reconcile_on_start">Выполнять полную сверку после запуска</NcCheckboxRadioSwitch>
            <NcCheckboxRadioSwitch v-model="settings.check_month">Проверять соответствие месяца листа папке</NcCheckboxRadioSwitch>
            <Field v-model="settings.timezone" label="Часовой пояс мероприятий (IANA)" />
            <label class="select-label">При исчезновении XLSX<select v-model="settings.missing_file_policy"><option value="future">Удалять только будущие события</option><option value="all">Удалять все связанные события</option><option value="keep">Не удалять автоматически</option></select></label>
          </NcSettingsSection>
          <NcSettingsSection name="Защита удаления" description="Перед массовым удалением приложение покажет список изменений и запросит подтверждение.">
            <NcCheckboxRadioSwitch v-model="settings.delete_guard">Защита от массового удаления</NcCheckboxRadioSwitch>
            <Field v-model="settings.delete_percent" label="Порог, %" numeric /><Field v-model="settings.delete_minimum" label="Минимум событий" numeric />
          </NcSettingsSection>
          <h2>Отслеживаемые файлы</h2><p v-if="!files.length">Список появится после первой фактической сверки.</p>
          <div class="table-wrap"><table v-if="files.length"><thead><tr><th>Файл</th><th>Состояние</th><th>Синхронизация</th></tr></thead><tbody><tr v-for="file in files" :key="file.file_id"><td>{{ file.path }}</td><td>{{ file.last_error || file.status }}</td><td>{{ date(file.last_sync_at) }}</td></tr></tbody></table></div>
        </template>

        <template v-if="settings && page === 'Поля Excel'">
          <NcSettingsSection name="Заголовки колонок" description="Порядок колонок произвольный. Дата берётся из имени листа, год — из ближайшей папки с годом.">
            <Field v-for="(label,key) in {location:'Место *',time:'Время *',title:'Название *',responsible:'Ответственный *',emails:'Email для уведомления',offsets:'Напомнить за',public:'Публиковать на сайте'}" :key="key" v-model="settings.headers[key]" :label="label" />
            <Field v-model="settings.duration_minutes" label="Продолжительность по умолчанию, минут (5–1440)" numeric />
            <p class="muted">Ответственный — произвольный текст. Email и интервалы напоминания задаются отдельно.</p>
            <NcButton :disabled="busy" @click="action(previewFile)">Проверить по существующему файлу</NcButton>
            <p class="muted">Сначала сохраните изменения заголовков. Проверка ничего не записывает в Calendar.</p>
          </NcSettingsSection>
        </template>

        <template v-if="settings && page === 'Календари'">
          <NcSettingsSection name="Внутренний календарь" description="Excel задаёт состояние календаря. Следующая сверка восстановит вручную изменённые или удалённые события.">
            <NcSelect v-model="settings.calendar_owner" :options="users" input-label="Владелец календарей" @update:model-value="action(loadCalendars)" />
            <NcSelect v-model="internal" :options="readyCalendars" label="name" input-label="Внутренний календарь" />
            <div class="actions"><Field v-model="newCalendarName" label="Название нового календаря" /><NcButton :disabled="busy || !settings.calendar_owner" @click="action(createCalendar)">Создать календарь</NcButton></div>
            <p class="muted">Доступ сотрудников настройте в штатном интерфейсе Nextcloud Calendar.</p>
          </NcSettingsSection>
          <NcSettingsSection name="Публичный календарь" description="Публикуются только строки с «На сайт = Да». Публичные события содержат название, время и место.">
            <NcCheckboxRadioSwitch v-model="settings.public_enabled">Формировать публичный календарь</NcCheckboxRadioSwitch>
            <NcSelect v-model="publicCalendar" :options="readyCalendars.filter(c=>c.url!==settings?.internal_calendar)" label="name" input-label="Публичный календарь" />
          </NcSettingsSection>
          <NcSettingsSection name="Публикация на сайте" description="Создайте публичную ссылку штатно в Calendar и вставьте её сюда.">
            <Field v-model="settings.public_link" label="Публичная ссылка Calendar" />
            <NcSelect v-model="embedView" :options="views" input-label="Режим отображения" />
            <Field v-model="embedDate" label="Дата (пусто — текущая)" type="date" />
            <pre class="embed-code">{{ iframe }}</pre>
            <NcButton :disabled="!iframe.startsWith('<iframe')" @click="action(copyIframe)">Скопировать iframe</NcButton>
          </NcSettingsSection>
        </template>

        <template v-if="settings && page === 'Уведомления'">
          <NcSettingsSection name="SMTP" description="Письма отправляются на адреса из Excel, включая получателей без аккаунта Nextcloud.">
            <NcCheckboxRadioSwitch v-model="settings.smtp_enabled">Email-уведомления</NcCheckboxRadioSwitch>
            <label class="select-label">Способ отправки<select v-model="settings.smtp_mode"><option value="nextcloud">SMTP Nextcloud</option><option value="custom">Отдельный SMTP</option></select></label>
            <p v-if="settings.smtp_mode==='nextcloud'">Отправка через SMTP, настроенный в Nextcloud. Отправитель и пароль берутся из настроек облака; изменения применяются без перенастройки приложения.</p>
            <template v-if="settings.smtp_mode==='custom'">
            <Field v-model="settings.smtp_host" label="SMTP-сервер" /><Field v-model="settings.smtp_port" label="Порт" numeric />
            <label class="select-label">Шифрование<select v-model="settings.smtp_security"><option value="starttls">STARTTLS</option><option value="tls">SSL/TLS</option><option value="none">Без шифрования</option></select></label>
            <p v-if="settings.smtp_security==='none'" class="error">Соединение и пароль передаются без шифрования. Используйте только доверенную сеть.</p>
            <Field v-model="settings.smtp_user" label="Логин" /><Field v-model="password" :label="settings.smtp_password_set ? 'Новый пароль (текущий сохранён)' : 'Пароль'" type="password" />
            <NcCheckboxRadioSwitch v-model="clearPassword">Удалить сохранённый пароль</NcCheckboxRadioSwitch>
            <Field v-model="settings.smtp_sender" label="Email отправителя" /><Field v-model="settings.smtp_name" label="Имя отправителя" />
            </template>
            <Field v-model="settings.overdue_minutes" label="Допустимая просрочка после простоя, минут (0 — пропустить)" numeric />
            <NcCheckboxRadioSwitch v-model="settings.notify_changes">Уведомлять об изменении после отправленного напоминания</NcCheckboxRadioSwitch>
            <NcCheckboxRadioSwitch v-model="settings.notify_cancellation">Уведомлять об отмене после отправленного напоминания</NcCheckboxRadioSwitch>
            <p class="muted">Изменение времени, даты или места вызывает отдельное письмо уже уведомлённым адресатам. При неоднозначном соответствии строк отправляется отмена старого события.</p>
          </NcSettingsSection>
          <NcSettingsSection name="Проверка отправки" description="Используются сохранённые настройки SMTP.">
            <Field v-model="testRecipient" label="Тестовый получатель" /><NcButton :disabled="busy || !testRecipient" @click="action(async()=>{await api('/api/smtp/test','POST',{recipient:testRecipient});showSuccess('Тестовое письмо отправлено')})">Отправить тестовое письмо</NcButton>
            <p class="muted">После аварийного прерывания отправки проверьте доставку перед ручным повтором: SMTP не поддерживает гарантию ровно одной доставки.</p>
          </NcSettingsSection>
        </template>

        <template v-if="settings && page === 'Журнал'">
          <div class="filters"><NcSelect v-model="logLevel" :options="['','Info','Warning','Error']" input-label="Уровень" /><NcSelect v-model="logSubsystem" :options="['','Events','Excel','Sync','Calendar','Email','AppAPI','Scheduler']" input-label="Подсистема" /><Field v-model="logFile" label="Файл (точный путь)" /><Field v-model="logSince" label="С" type="datetime-local" /><Field v-model="logUntil" label="По" type="datetime-local" /></div>
          <div class="actions"><NcButton @click="action(async()=>{logOffset=0;await loadLogs()})">Применить фильтры</NcButton><NcButton @click="action(downloadLogs)">Скачать журнал</NcButton><NcButton @click="clearDialog=true">Очистить журнал</NcButton></div>
          <label class="select-label">Хранить журнал<select v-model.number="settings.retention_days"><option v-for="n in [30,90,180,365]" :key="n" :value="n">{{ n }} дней</option></select></label>
          <p v-if="!logs.length">Записей по выбранным фильтрам нет.</p><div class="table-wrap"><table v-if="logs.length"><thead><tr><th>Дата</th><th>Уровень</th><th>Подсистема</th><th>Файл / лист</th><th>Операция</th><th>Результат</th></tr></thead><tbody><tr v-for="log in logs" :key="log.id"><td>{{ date(log.at) }}</td><td>{{ log.level }}</td><td>{{ log.subsystem }}</td><td>{{ log.file }} {{ log.sheet }}</td><td>{{ log.operation }}</td><td>{{ log.result }}</td></tr></tbody></table></div>
          <div class="actions"><NcButton :disabled="!logOffset" @click="action(async()=>{logOffset-=100;await loadLogs()})">Назад</NcButton><NcButton :disabled="logs.length<100" @click="action(async()=>{logOffset+=100;await loadLogs()})">Далее</NcButton></div>
        </template>
        <div v-if="settings && page !== 'Обзор'" class="save-bar"><NcButton variant="primary" :disabled="busy || !!activeRun" @click="action(save)">Сохранить настройки</NcButton></div>
      </main>
    </NcAppContent>
    <NcModal v-if="details" name="Результат сверки" @close="details=undefined">
      <div class="modal-content">
<h2>{{ stateName(details.status) }}</h2><p v-if="details.result?.message" class="error">{{ details.result.message }}</p>
        <p v-if="['queued','running'].includes(details.status)">Читаем XLSX и сравниваем с календарями…</p>
        <template v-if="details.result?.internal"><p>Внутренний: создать {{ details.result.internal.create }}, обновить {{ details.result.internal.update }}, удалить {{ details.result.internal.delete }}.</p><p>Публичный: создать {{ details.result.public?.create }}, обновить {{ details.result.public?.update }}, удалить {{ details.result.public?.delete }}. Писем в очередь: {{ details.result.email_jobs }}.</p></template>
        <ul v-if="details.result?.issues?.length"><li v-for="(issue,i) in details.result.issues" :key="i">{{ issue.level }}: {{ issue.file }} {{ issue.sheet }} — {{ issue.message }}</li></ul>
        <div v-if="details.result?.operations?.length" class="table-wrap"><table><thead><tr><th>Календарь</th><th>Действие</th><th>Файл / лист</th><th>Причина</th></tr></thead><tbody><tr v-for="(op,i) in details.result.operations" :key="i"><td>{{ op.target }}</td><td>{{ op.action }}</td><td>{{ op.file_id }} / {{ op.sheet }}</td><td>{{ op.reason }}<small>{{ op.source_key }}</small></td></tr></tbody></table></div>
        <div v-if="details.status==='requires_confirmation'" class="actions"><NcButton variant="error" :disabled="busy" @click="action(()=>run(`/api/runs/${details?.id}/confirm`))">Подтвердить удаление</NcButton><NcButton :disabled="busy" @click="action(async()=>{await api(`/api/runs/${details?.id}/cancel`,'POST');await refresh()})">Отменить</NcButton></div>
      </div>
    </NcModal>
    <NcModal v-if="preview" name="Проверка Excel" @close="preview=undefined"><div class="modal-content"><h2>Проверка Excel</h2><p>{{ preview.file }} · Год {{ preview.year }}</p><ul><li v-for="sheet in preview.sheets" :key="sheet.name">{{ sheet.name }} — {{ sheet.date || 'Пропущен' }}<span v-if="sheet.headers"> · {{ Object.keys(sheet.headers).join(', ') }}</span></li></ul><ul><li v-for="(issue,i) in preview.issues" :key="i">{{ issue.level }}: {{ issue.sheet }} / {{ issue.row }} — {{ issue.message }}</li></ul><div class="table-wrap"><table><thead><tr><th>Название</th><th>Начало</th><th>Окончание</th><th>Место</th><th>Ответственный</th></tr></thead><tbody><tr v-for="(event,i) in preview.events" :key="i"><td>{{ event.title }}</td><td>{{ date(event.start) }}</td><td>{{ date(event.end) }}</td><td>{{ event.location }}</td><td>{{ event.responsible }}</td></tr></tbody></table></div></div></NcModal>
    <NcModal v-if="clearDialog" name="Очистить журнал" @close="clearDialog=false"><div class="modal-content"><h2>Очистить журнал?</h2><p>Все сохранённые записи журнала будут удалены.</p><div class="actions"><NcButton variant="error" @click="action(async()=>{await api('/api/logs','DELETE');clearDialog=false;await loadLogs()})">Очистить</NcButton><NcButton @click="clearDialog=false">Отмена</NcButton></div></div></NcModal>
  </NcContent>
</template>

<style>
.events-page{padding:32px clamp(16px,4vw,48px);max-width:1300px;margin:auto}.page-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}.eyebrow{color:var(--color-text-maxcontrast);font-size:14px;margin:0 0 8px}h1{font-size:28px;font-weight:600}h2{font-size:20px;font-weight:600;margin:28px 0 16px}.intro{max-width:740px;margin-bottom:24px;color:var(--color-text-maxcontrast);line-height:1.7}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.cards article{padding:20px;border:1px solid var(--color-border);border-radius:var(--border-radius-large,12px);background:var(--color-background-hover)}.cards span,.cards small{display:block;color:var(--color-text-maxcontrast)}.cards strong{display:block;font-size:30px;margin:12px 0;line-height:1.2}dl{display:grid;grid-template-columns:220px 1fr;gap:12px;margin:24px 0}dt{color:var(--color-text-maxcontrast)}dd{overflow-wrap:anywhere}.actions{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:16px 0}.actions .input-field{flex:1;min-width:200px}.input-field,.v-select{max-width:640px;margin:12px 0}.select-label{display:flex;flex-direction:column;gap:8px;margin:16px 0;max-width:640px}select{padding:10px;border:1px solid var(--color-border);border-radius:8px;background:var(--color-main-background);color:var(--color-main-text);width:100%}.muted{color:var(--color-text-maxcontrast);max-width:700px;line-height:1.6;margin:12px 0}.error{color:var(--color-error);padding:12px 0;overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:12px 10px;border-bottom:1px solid var(--color-border);vertical-align:top}th{color:var(--color-text-maxcontrast);font-weight:600}td small{display:block;font-size:11px;overflow-wrap:anywhere}.save-bar{position:sticky;bottom:0;padding:16px 0;background:var(--color-main-background);border-top:1px solid var(--color-border);margin-top:24px}.modal-content{padding:28px;min-width:0;max-width:960px}.modal-content ul{padding-left:20px;margin:16px 0}.modal-content li{margin:8px 0;overflow-wrap:anywhere}.embed-code{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--color-background-hover);padding:16px;border-radius:8px;max-width:740px}.diagnostics{max-width:650px;padding:16px;border:1px solid var(--color-border);border-radius:12px}.diagnostics li{display:flex;justify-content:space-between;padding:8px}.filters{display:flex;gap:16px;flex-wrap:wrap}.filters>*{flex:1;min-width:160px}@media(max-width:900px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}dl{grid-template-columns:1fr;gap:4px}dd{margin-bottom:12px}}@media(max-width:480px){.cards{grid-template-columns:1fr}.events-page{padding:20px 16px}.modal-content{padding:16px}}
</style>
