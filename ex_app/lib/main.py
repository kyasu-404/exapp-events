import asyncio
import csv
import json
import os
import time
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import AppAPIAuthMiddleware, anc_app, run_app, set_handlers
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .auth import SecretGuard, require_admin
from .models import Settings
from .runtime import Runtime
from .services import smtp_service
from .services.calendar_service import CalendarService
from .services.event_listener import CALLBACK, enqueue
from .services.excel_parser import parse_xlsx
from .services.mail_delivery import MailDelivery
from .services.nextcloud_files import NextcloudFiles

Admin = Annotated[AsyncNextcloudApp, Depends(require_admin)]


def create_app(directory: Path | None = None, factory=AsyncNextcloudApp, restore=True):
    @asynccontextmanager
    async def lifespan(app):
        path = directory or Path(os.environ["APP_PERSISTENT_STORAGE"])
        app.state.runtime = Runtime(path, factory)
        if restore:
            app.state.runtime.spawn(app.state.runtime.restore())
        yield
        await app.state.runtime.enable(False, factory())
        app.state.runtime.store.close()

    app = FastAPI(title="Мероприятия", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(AppAPIAuthMiddleware)
    app.add_middleware(SecretGuard)

    async def enabled_handler(enabled, nc):
        try:
            await app.state.runtime.enable(enabled, nc)
            return ""
        except Exception:
            app.state.runtime.store.log(
                "Error",
                "AppAPI",
                "Не удалось зарегистрировать интерфейс/события; проверьте Webhook Listeners",
            )
            return "Не удалось включить приложение. Проверьте AppAPI и Webhook Listeners."

    set_handlers(app, enabled_handler, default_init=False, map_app_static=False)

    @app.post("/init")
    async def init(tasks: BackgroundTasks, nc: Annotated[AsyncNextcloudApp, Depends(anc_app)]):
        tasks.add_task(nc.set_init_status, 100)
        return {}

    @app.post(CALLBACK)
    async def event(request: Request):
        runtime = app.state.runtime
        if not runtime.enabled:
            return {"queued": False}
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(422, "Ожидается объект события")
        settings = runtime.settings()
        settings.source_path = runtime.store.meta("source_path", settings.source_path)
        return {"queued": enqueue(runtime.store, payload, settings)}

    api = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])

    @api.get("/settings")
    async def get_settings(nc: Admin):
        result = app.state.runtime.settings().model_dump()
        result["source_path"] = app.state.runtime.store.meta("source_path", result["source_path"])
        result["archive_path"] = app.state.runtime.store.meta("archive_path", result["archive_path"])
        result["smtp_password_set"] = bool(await nc.appconfig_ex.get_value("smtp_password", ""))
        return result

    class SettingsUpdate(BaseModel):
        model_config = ConfigDict(extra="forbid")
        settings: Settings
        smtp_password: SecretStr | None = None
        clear_smtp_password: bool = False

    @api.put("/settings")
    async def put_settings(body: SettingsUpdate, nc: Admin):
        runtime, settings = app.state.runtime, body.settings
        if runtime.store.one("SELECT id FROM sync_runs WHERE status IN ('queued','running')"):
            raise HTTPException(409, "Дождитесь завершения сверки перед изменением настроек")
        old = runtime.settings()
        if not settings.archive_path:
            settings.archive_id = ""
        elif (
            settings.archive_path != runtime.store.meta("archive_path", old.archive_path)
            and settings.archive_id == old.archive_id
        ):
            settings.archive_id = ""
        if runtime.store.one("SELECT source_key FROM source_events LIMIT 1"):
            # Prevent silent migration/abandoning already generated events in another Calendar/owner.
            if (
                old.source_owner,
                old.source_id,
                old.calendar_owner,
                old.internal_calendar,
            ) != (
                settings.source_owner,
                settings.source_id,
                settings.calendar_owner,
                settings.internal_calendar,
            ) or (old.public_calendar and old.public_calendar != settings.public_calendar):
                raise HTTPException(
                    409,
                    "Владельцев, источник и выбранные календари нельзя заменять после первой синхронизации. Для переноса требуется отдельная миграция состояния.",
                )
        if settings.smtp_enabled and settings.smtp_mode == "custom":
            try:
                smtp_service.message({"kind": "test"}, settings.smtp_sender, settings, "validation")
            except Exception:
                raise HTTPException(422, "Проверьте email отправителя") from None
            if not settings.smtp_host:
                raise HTTPException(422, "Укажите SMTP-сервер")
        if settings.source_path:
            client = factory()
            await client.set_user(settings.source_owner)
            root = await NextcloudFiles(client).resolve_root(settings)
            settings.source_id, settings.source_path = str(root.info.fileid), root.user_path
            archive = await NextcloudFiles(client).resolve_archive(settings, root)
            if archive:
                settings.archive_id, settings.archive_path = str(archive.info.fileid), archive.user_path
        elif settings.archive_path:
            raise HTTPException(422, "Сначала выберите папку-источник")
        if settings.internal_calendar or settings.public_calendar:
            client = factory()
            await client.set_user(settings.calendar_owner)
            calendar = CalendarService(client, settings.calendar_owner)
            settings.internal_calendar = (
                calendar.safe_path(settings.internal_calendar) if settings.internal_calendar else ""
            )
            settings.public_calendar = (
                calendar.safe_path(settings.public_calendar) if settings.public_calendar else ""
            )
            if settings.public_calendar and settings.internal_calendar == settings.public_calendar:
                raise HTTPException(422, "Внутренний и публичный календари должны различаться")
            available = {c["url"] for c in await calendar.list_calendars() if c["writable"]}
            if any(
                c and calendar.safe_path(c) not in available
                for c in [settings.internal_calendar, settings.public_calendar]
            ):
                raise HTTPException(422, "Календарь недоступен для записи")
        if body.clear_smtp_password:
            await nc.appconfig_ex.delete("smtp_password")
        elif body.smtp_password is not None and body.smtp_password.get_secret_value():
            await nc.appconfig_ex.set_value(
                "smtp_password", body.smtp_password.get_secret_value(), sensitive=True
            )
        runtime.store.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (settings.model_dump_json(),))
        runtime.store.set_meta("archive_path", settings.archive_path)
        if old.delete_guard and not settings.delete_guard:
            runtime.store.execute("UPDATE sync_runs SET status='superseded' WHERE status='requires_confirmation'")
        if not settings.smtp_enabled:
            runtime.store.execute(
                "UPDATE reminders SET status='cancelled' WHERE status IN ('pending','retry')"
            )
        if (old.archive_path, old.archive_id, old.delete_guard) != (
            settings.archive_path,
            settings.archive_id,
            settings.delete_guard,
        ):
            runtime.store.execute(
                "INSERT OR REPLACE INTO event_queue VALUES(?,?,?)",
                ("settings", time.time(), '{"subtype":"SettingsChanged"}'),
            )
        return {"saved": True}

    @api.get("/status")
    async def status():
        runtime = app.state.runtime
        counts = {
            "files": runtime.store.one(
                "SELECT COUNT(*) n FROM source_files WHERE status NOT IN ('missing','archived')"
            )["n"],
            "events": runtime.store.one("SELECT COUNT(*) n FROM source_events WHERE active=1")["n"],
            "public": runtime.store.one(
                "SELECT COUNT(*) n FROM calendar_events WHERE target='public' AND status='ok'"
            )["n"],
        }
        counts["email"] = runtime.store.rows("SELECT status,COUNT(*) n FROM reminders GROUP BY status")
        counts["logs"] = runtime.store.rows("SELECT level,COUNT(*) n FROM logs GROUP BY level")
        counts["issues"] = len(json.loads(runtime.store.meta("sync_issues", "[]")))
        return {
            "enabled": runtime.enabled,
            "version": "0.1.3",
            "counts": counts,
            "source_path": runtime.store.meta("source_path", runtime.settings().source_path),
            "last_files_event": runtime.store.meta("last_files_event"),
            "last_full_reconciliation": runtime.store.meta("last_full_reconciliation"),
            "last_successful_sync": runtime.store.meta("last_successful_sync"),
            "issues": json.loads(runtime.store.meta("sync_issues", "[]")),
            "recent_emails": runtime.store.rows(
                "SELECT id,recipient,scheduled_at,status,last_error,sent_at,kind FROM reminders ORDER BY id DESC LIMIT 20"
            ),
            "runs": [
                runtime.run(r["id"])
                for r in runtime.store.rows("SELECT id FROM sync_runs ORDER BY started_at DESC LIMIT 10")
            ],
        }

    @api.post("/sync", status_code=202)
    async def sync():
        return {"run_id": app.state.runtime.start_run()}

    @api.post("/dry-run", status_code=202)
    async def dry_run():
        return {"run_id": app.state.runtime.start_run("dry_run")}

    @api.get("/runs/{run_id}")
    async def run(run_id: str):
        return app.state.runtime.run(run_id)

    @api.post("/runs/{run_id}/confirm", status_code=202)
    async def confirm(run_id: str):
        runtime = app.state.runtime
        row = runtime.run(run_id)
        if row["status"] != "requires_confirmation":
            raise HTTPException(409, "Сверка не ожидает подтверждения")
        result = runtime.start_run("confirmed", row["result"]["fingerprint"])
        runtime.store.execute("UPDATE sync_runs SET status='confirmation_queued' WHERE id=?", (run_id,))
        return {"run_id": result}

    @api.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        changed = app.state.runtime.store.execute(
            "UPDATE sync_runs SET status='cancelled' WHERE id=? AND status='requires_confirmation'", (run_id,)
        ).rowcount
        if not changed:
            raise HTTPException(409, "Сверка не ожидает подтверждения")
        return {"cancelled": True}

    @api.get("/files")
    async def files():
        return app.state.runtime.store.rows("SELECT * FROM source_files ORDER BY path")

    class FilePreview(BaseModel):
        file_id: str

    @api.post("/files/preview")
    async def preview(body: FilePreview):
        runtime = app.state.runtime
        settings = runtime.settings()
        client = factory()
        await client.set_user(settings.source_owner)
        files = NextcloudFiles(client)
        file = await files.preview_file(body.file_id, settings)
        parsed = await asyncio.to_thread(
            parse_xlsx, await files.read(file), file.file_id, file.path, settings
        )
        return {
            "file": file.path,
            "year": parsed.year,
            "sheets": parsed.sheets,
            "events": [e.data() for e in parsed.events[:10]],
            "issues": parsed.issues,
        }

    @api.get("/users")
    async def users(nc: Admin):
        return await nc.users_list()

    @api.get("/calendars")
    async def calendars(owner: str):
        client = factory()
        await client.set_user(owner)
        return await CalendarService(client, owner).list_calendars()

    class CreateCalendar(BaseModel):
        owner: str
        name: str = Field(min_length=1, max_length=200)

    @api.post("/calendars", status_code=201)
    async def create_calendar(body: CreateCalendar):
        client = factory()
        await client.set_user(body.owner)
        return await CalendarService(client, body.owner).create(body.name)

    class Recipient(BaseModel):
        recipient: str = Field(max_length=254)

    @api.post("/smtp/test")
    async def smtp_test(body: Recipient, nc: Admin):
        settings = app.state.runtime.settings()
        password = (
            "" if settings.smtp_mode == "nextcloud" else await nc.appconfig_ex.get_value("smtp_password", "")
        )
        try:
            await MailDelivery(factory).send({"kind": "test"}, body.recipient, settings, password, "test")
        except Exception:
            raise HTTPException(
                502, "Тест SMTP не выполнен: проверьте сервер, шифрование, авторизацию и адреса"
            ) from None
        return {"sent": True}

    @api.post("/retry-failed", status_code=202)
    async def retry():
        runtime = app.state.runtime
        run_id = runtime.start_run("retry")
        runtime.store.execute(
            "UPDATE reminders SET status='retry',attempt_count=0,next_attempt_at=NULL WHERE status='failed'"
        )
        return {"run_id": run_id}

    @api.post("/diagnostics")
    async def diagnostics(nc: Admin):
        runtime = app.state.runtime
        settings = runtime.settings()
        result = []

        async def check(name, operation):
            try:
                await operation()
                result.append({"name": name, "status": "ok"})
            except Exception:
                result.append(
                    {"name": name, "status": "error", "message": "Проверьте настройки и доступность"}
                )

        await check("AppAPI", lambda: nc.ocs("GET", "/ocs/v1.php/apps/app_api/ex-app/state"))

        async def listener_check():
            from .services.event_listener import EVENTS

            listeners = await nc.webhooks.get_list(CALLBACK)
            registered = {hook.event for hook in listeners if hook.app_id == nc.app_cfg.app_name}
            if not all("OCP\\Files\\Events\\Node\\" + event in registered for event in EVENTS):
                raise ValueError("Файловые события не зарегистрированы")

        await check("Events Listener (Webhook Listeners)", listener_check)
        result.append(
            {
                "name": "Persistent storage",
                "status": "ok" if os.access(runtime.store.path.parent, os.W_OK) else "error",
            }
        )
        result.append(
            {
                "name": "SQLite",
                "status": "ok" if runtime.store.one("PRAGMA quick_check")["quick_check"] == "ok" else "error",
            }
        )

        async def source_check():
            client = factory()
            await client.set_user(settings.source_owner)
            await NextcloudFiles(client).resolve_root(settings)

        await check("Папка-источник", source_check)

        async def calendar_check():
            client = factory()
            await client.set_user(settings.calendar_owner)
            await CalendarService(client, settings.calendar_owner).current(
                settings.internal_calendar, "internal"
            )

        await check("Calendar API", calendar_check)
        if settings.smtp_mode == "nextcloud" or settings.smtp_host:

            async def smtp_check():
                password = (
                    ""
                    if settings.smtp_mode == "nextcloud"
                    else await nc.appconfig_ex.get_value("smtp_password", "")
                )
                await MailDelivery(factory).probe(settings, password)

            await check("SMTP", smtp_check)
        else:
            result.append({"name": "SMTP", "status": "not_configured"})
        return result

    @api.get("/logs")
    async def logs(
        level: str = "",
        subsystem: str = "",
        file: str = "",
        since: str = "",
        until: str = "",
        export: bool = False,
        offset: int = 0,
    ):
        if offset < 0:
            raise HTTPException(422, "Некорректная страница")
        query, params = "SELECT * FROM logs WHERE 1=1", []
        for column, value, operator in [
            ("level", level, "="),
            ("subsystem", subsystem, "="),
            ("file", file, "="),
            ("at", since, ">="),
            ("at", until, "<="),
        ]:
            if value:
                query += f" AND {column}{operator}?"
                params.append(value)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        rows = app.state.runtime.store.rows(query, (*params, 10000 if export else 100, offset))
        if export:
            buffer = StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["Дата", "Уровень", "Подсистема", "Файл", "Лист", "Операция", "Результат"])
            for row in rows:
                writer.writerow(
                    [
                        ("'" + str(row[k]) if str(row[k]).startswith(("=", "+", "-", "@")) else row[k])
                        for k in ("at", "level", "subsystem", "file", "sheet", "operation", "result")
                    ]
                )
            return Response(
                "\ufeff" + buffer.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="events-log.csv"'},
            )
        return rows

    @api.delete("/logs")
    async def clear_logs():
        app.state.runtime.store.execute("DELETE FROM logs")
        return {"cleared": True}

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"message": str(exc)}, status_code=422)

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        return JSONResponse(
            {"message": "Операция не выполнена. Проверьте конфигурацию и журнал."}, status_code=502
        )

    # Pydantic's default error includes raw input. Exclude input so password validation cannot echo secrets.
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            {
                "message": "Некорректные настройки",
                "errors": [{"field": list(e["loc"]), "message": e["msg"]} for e in exc.errors()],
            },
            status_code=422,
        )

    app.include_router(api)
    base = Path(__file__).resolve().parents[1]
    for name in ("js", "css"):
        path = base / name
        if path.exists():
            router = APIRouter(dependencies=[Depends(require_admin)])

            @router.get(f"/{name}/{{filename}}")
            async def asset(request: Request, filename: str):
                directory = base / request.url.path.strip("/").split("/")[0]
                if Path(filename).name != filename:
                    raise HTTPException(404)
                from fastapi.responses import FileResponse

                return FileResponse(directory / filename)

            app.include_router(router)
    app.mount("/img", StaticFiles(directory=base / "img"), name="img")
    return app


APP = create_app()

if __name__ == "__main__":
    run_app("ex_app.lib.main:APP", log_level="info")
