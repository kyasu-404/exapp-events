import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nc_py_api import AsyncNextcloudApp

from .db.store import Store, dumps, now
from .models import Settings
from .services.calendar_service import CalendarService
from .services.event_listener import register_listener
from .services.mail_delivery import MailDelivery
from .services.nextcloud_files import NextcloudFiles
from .services.reminder_service import ReminderService
from .services.sync_engine import SyncEngine


class Runtime:
    def __init__(self, directory: Path, factory=AsyncNextcloudApp):
        self.store = Store(directory)
        self.factory = factory
        self.reminders = ReminderService(self.store, MailDelivery(factory))
        self.engine = SyncEngine(self.store, self.reminders)
        self.tasks = set()
        self.scheduler = None
        self.enabled = False
        self.last_periodic = 0
        self.enable_lock = asyncio.Lock()

    def settings(self):
        row = self.store.one("SELECT data FROM settings WHERE id=1")
        return Settings.model_validate_json(row["data"]) if row else Settings()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def clients(self, settings):
        files_nc, calendar_nc = self.factory(), self.factory()
        await files_nc.set_user(settings.source_owner)
        await calendar_nc.set_user(settings.calendar_owner)
        return NextcloudFiles(files_nc), CalendarService(calendar_nc, settings.calendar_owner)

    async def execute(self, run_id, dry_run=False, confirmation=None):
        settings = self.settings()
        try:
            files, calendars = await self.clients(settings)
            await self.engine.run(run_id, settings, files, calendars, dry_run, confirmation)
        except asyncio.CancelledError:
            self.store.execute(
                "UPDATE sync_runs SET status='interrupted',finished_at=? WHERE id=?", (now(), run_id)
            )
            raise
        except Exception:
            self.store.execute(
                "UPDATE sync_runs SET status='failed',finished_at=?,errors=1,result=? WHERE id=?",
                (now(), dumps({"message": "Nextcloud недоступен; проверьте конфигурацию"}), run_id),
            )
            self.store.log("Error", "AppAPI", "Nextcloud недоступен; проверьте конфигурацию")

    def start_run(self, kind="manual", confirmation=None):
        if not self.enabled:
            raise ValueError("Приложение отключено")
        if self.store.one("SELECT id FROM sync_runs WHERE status IN ('queued','running')"):
            raise ValueError("Сверка уже выполняется")
        run_id = self.engine.create_run(kind)
        self.spawn(self.execute(run_id, kind == "dry_run", confirmation))
        return run_id

    async def enable(self, enabled: bool, nc):
        async with self.enable_lock:
            if not enabled:
                self.enabled = False
                if self.scheduler:
                    self.scheduler.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self.scheduler
                    self.scheduler = None
                # Let an in-flight SMTP send finish so acceptance can be recorded before shutdown.
                async with self.reminders.lock:
                    pass
                tasks = list(self.tasks)
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                self.store.set_meta("enabled", "0")
                return
            if self.enabled:
                return
            await nc.ui.top_menu.register("events", "Мероприятия", "img/app.svg", admin_required=True)
            await nc.ui.resources.set_script("top_menu", "events", "js/events-main")
            await nc.ui.resources.set_style("top_menu", "events", "css/events-main")
            await register_listener(nc)
            self.store.set_meta("listener", "Webhook Listeners / AppAPI")
            self.store.recover()
            self.reminders.recover_overdue(self.settings())
            self.enabled = True
            self.store.set_meta("enabled", "1")
            self.last_periodic = time.time()
            self.scheduler = asyncio.create_task(self.schedule())
            settings = self.settings()
            if settings.reconcile_on_start and settings.source_path and settings.internal_calendar:
                self.start_run("startup")

    async def restore(self):
        # Cloud maintenance may last longer than a container restart. Keep reconnecting
        # until the server can confirm enabled state; shutdown cancels this task.
        delay = 0
        while True:
            await asyncio.sleep(delay)
            try:
                nc = self.factory()
                enabled = bool(await nc.ocs("GET", "/ocs/v1.php/apps/app_api/ex-app/state"))
                if enabled:
                    await self.enable(True, nc)
                return
            except Exception:
                self.store.log("Warning", "AppAPI", "Не удалось восстановить lifecycle; повтор подключения")
                delay = min(60, max(5, delay * 2))

    async def schedule(self):
        while self.enabled:
            try:
                settings = self.settings()
                current = time.time()
                due = self.store.rows("SELECT file_id,due_at FROM event_queue WHERE due_at<=?", (current,))
                periodic = (
                    settings.reconciliation_minutes
                    and current - self.last_periodic >= settings.reconciliation_minutes * 60
                )
                # Retry failed file/calendar operations without waiting 24h if periodic scans are disabled.
                latest = self.store.one(
                    "SELECT status FROM sync_runs WHERE type!='dry_run' ORDER BY started_at DESC LIMIT 1"
                )
                retry_due = (
                    current - self.last_periodic >= 60
                    and latest
                    and latest["status"] in {"partial", "failed"}
                )
                busy = self.store.one("SELECT id FROM sync_runs WHERE status IN ('queued','running')")
                if (
                    (due or periodic or retry_due)
                    and not busy
                    and settings.source_path
                    and settings.internal_calendar
                ):
                    self.start_run("event" if due else "periodic")
                    self.last_periodic = current
                    for item in due:
                        self.store.execute(
                            "DELETE FROM event_queue WHERE file_id=? AND due_at=?",
                            (item["file_id"], item["due_at"]),
                        )
                if settings.smtp_enabled and not any(t.get_name() == "smtp" for t in self.tasks):
                    self.spawn(self.mail_tick(settings)).set_name("smtp")
                threshold = (datetime.now(UTC) - timedelta(days=settings.retention_days)).isoformat()
                self.store.execute("DELETE FROM logs WHERE at<?", (threshold,))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.store.log("Error", "Scheduler", "Ошибка фоновой задачи; повтор через 5 секунд")
            await asyncio.sleep(5)

    async def mail_tick(self, settings):
        try:
            password = (
                ""
                if settings.smtp_mode == "nextcloud"
                else await self.factory().appconfig_ex.get_value("smtp_password", "")
            )
            await self.reminders.tick(settings, password)
        except Exception:
            self.store.log("Error", "Email", "SMTP secret недоступен; отправка отложена")

    def run(self, run_id):
        row = self.store.one("SELECT * FROM sync_runs WHERE id=?", (run_id,))
        if not row:
            raise ValueError("Сверка не найдена")
        row["result"] = json.loads(row["result"]) if row["result"] else None
        return row
