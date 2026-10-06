import asyncio
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from ..db.store import dumps, now
from ..models import Event, digest
from .calendar_service import canonical_ical, event_ical, uid
from .excel_parser import parse_xlsx


class SyncEngine:
    def __init__(self, store, reminders):
        self.store, self.reminders = store, reminders
        self.lock = asyncio.Lock()
        self.file_locks = defaultdict(asyncio.Lock)
        self.semaphore = asyncio.Semaphore(3)

    async def build(self, settings, files, calendars):
        if (
            not settings.source_owner
            or not settings.source_path
            or not settings.calendar_owner
            or not settings.internal_calendar
        ):
            raise ValueError("Выберите источник и внутренний календарь")
        root_path, sources = await files.scan(settings)
        old_rows = self.store.rows("SELECT * FROM source_events")
        old = {r["source_key"]: Event(**json.loads(r["data"])) for r in old_rows}
        active = {r["source_key"] for r in old_rows if r["active"]}
        desired, protected, parsed_files, issues = {}, set(), [], []

        async def parse_file(file):
            async with self.semaphore, self.file_locks[file.file_id]:
                try:
                    content = await files.read(file)
                    parsed = await asyncio.to_thread(parse_xlsx, content, file.file_id, file.path, settings)
                    if not parsed.safe_delete:
                        protected.add(file.file_id)
                    for issue in parsed.issues:
                        issues.append({**issue, "file": file.path, "subsystem": "Excel"})
                    for event in parsed.events:
                        previous = old.get(event.source_key)
                        event.revision = (
                            previous.revision
                            + (previous.hash() != event.hash() or event.source_key not in active)
                            if previous
                            else 1
                        )
                        desired[event.source_key] = event
                    parsed_files.append(
                        {
                            "file_id": file.file_id,
                            "path": file.path,
                            "etag": file.etag,
                            "mtime": file.mtime,
                            "content_hash": hashlib.sha256(content).hexdigest(),
                            "status": "warning" if parsed.issues else "ok",
                            "last_error": parsed.issues[0]["message"] if parsed.issues else "",
                        }
                    )
                except Exception:
                    protected.add(file.file_id)
                    issues.append(
                        {
                            "level": "Error",
                            "message": "Файл временно недоступен или изменился во время чтения; прежние события сохранены",
                            "file": file.path,
                            "sheet": "",
                            "subsystem": "Excel",
                        }
                    )
                    parsed_files.append(
                        {
                            "file_id": file.file_id,
                            "path": file.path,
                            "etag": file.etag,
                            "mtime": file.mtime,
                            "content_hash": "",
                            "status": "error",
                            "last_error": issues[-1]["message"],
                        }
                    )

        await asyncio.gather(*(parse_file(file) for file in sources))
        present = {s.file_id for s in sources}
        archive_candidates = {e.file_id for e in old.values()} | {
            r["file_id"] for r in self.store.rows("SELECT file_id FROM source_files")
        }
        archive_candidates -= present
        archived = await files.archived_files(archive_candidates) if settings.archive_path else {}
        at = datetime.now(UTC)
        for key, event in old.items():
            if event.file_id in archived:
                continue
            if key in desired:
                continue
            if key not in active:
                continue
            preserve = (
                event.file_id in protected
                or (event.file_id in present and event.starts_at <= at)
                or (
                    event.file_id not in present
                    and (
                        settings.missing_file_policy == "keep"
                        or (settings.missing_file_policy == "future" and event.starts_at <= at)
                    )
                )
            )
            if preserve:
                desired[key] = event
        deleted = active - set(desired)
        operations, unavailable, remote_maps = [], set(), {}
        # Failure of a target only blocks that target. Unreadable calendars must never appear empty.
        for target, url in [("internal", settings.internal_calendar), ("public", settings.public_calendar)]:
            if not url:
                continue
            try:
                remote_maps[target] = await calendars.current(url, target)
            except Exception:
                unavailable.add(target)
                issues.append(
                    {
                        "level": "Error",
                        "message": f"Календарь {target} недоступен; операции будут повторены при следующей сверке",
                        "file": "",
                        "sheet": "",
                        "subsystem": "Calendar",
                    }
                )
        if settings.archive_path:
            # Internal CalDAV metadata also identifies archived files after SQLite state loss.
            extra_candidates = {
                row["file_id"]
                for current in remote_maps.values()
                for row in current.values()
                if row.get("file_id") and row["file_id"] not in present
            }
            extra_candidates -= archive_candidates
            archive_candidates |= extra_candidates
            archived.update(await files.archived_files(extra_candidates))
        for target, current in remote_maps.items():
            url = settings.internal_calendar if target == "internal" else settings.public_calendar
            wanted = {
                uid(event, target): event
                for event in desired.values()
                if target == "internal" or (settings.public_enabled and event.public)
            }
            for event_uid, event in wanted.items():
                content = event_ical(event, target)
                existing = current.get(event_uid)
                if existing and existing["hash"] == canonical_ical(content):
                    continue
                operations.append(
                    {
                        "target": target,
                        "action": "update" if existing else "create",
                        "uid": event_uid,
                        "source_key": event.source_key,
                        "file_id": event.file_id,
                        "sheet": event.sheet,
                        "reason": "Excel отличается от Calendar"
                        if existing
                        else "Событие отсутствует в Calendar",
                        "href": existing["href"] if existing else url.rstrip("/") + "/" + event_uid + ".ics",
                        "etag": existing["etag"] if existing else "",
                        "ical": content.decode(),
                    }
                )
            for event_uid, existing in current.items():
                if event_uid in wanted:
                    continue
                key = existing.get("source_key") or event_uid.removeprefix(f"exapp-events-{target}-")
                known = old.get(key)
                internal_remote = remote_maps.get("internal", {}).get("exapp-events-internal-" + key, {})
                file_id = existing.get("file_id") or (
                    known.file_id if known else internal_remote.get("file_id", "")
                )
                if file_id in protected:
                    continue
                if target == "public" and not known and not internal_remote and key not in desired:
                    # No private metadata in public events: do not guess ownership after loss of BOTH stores.
                    issues.append(
                        {
                            "level": "Warning",
                            "message": "Публичное событие без восстановимой связи сохранено; требуется ручная проверка",
                            "file": "",
                            "sheet": "",
                            "subsystem": "Calendar",
                        }
                    )
                    continue
                start = datetime.fromisoformat(existing["start"])
                if key not in desired and file_id not in archived:
                    if file_id in present and start <= at:
                        continue
                    if file_id not in present and (
                        settings.missing_file_policy == "keep"
                        or (settings.missing_file_policy == "future" and start <= at)
                    ):
                        continue
                operations.append(
                    {
                        "target": target,
                        "action": "delete",
                        "uid": event_uid,
                        "source_key": key,
                        "file_id": file_id,
                        "sheet": known.sheet if known else "",
                        "reason": "Строка/файл исчезли или публикация выключена",
                        "href": existing["href"],
                        "etag": existing["etag"],
                        "ical": "",
                    }
                )
        moved = self.match_moves(old, desired, deleted & active)
        email_jobs = []
        for event in desired.values():
            email_jobs.extend(self.reminders.jobs(event, settings, at))
            previous = old.get(event.source_key)
            previous_key = next((k for k, v in moved.items() if v == event.source_key), None)
            if (
                settings.smtp_enabled
                and settings.notify_changes
                and (
                    (
                        previous
                        and (previous.start, previous.end, previous.location)
                        != (event.start, event.end, event.location)
                    )
                    or previous_key
                )
            ):
                source = old[previous_key] if previous_key else previous
                for recipient in self.reminders.sent_recipients(uid(source, "internal")):
                    email_jobs.append(self.reminders.notice(event, "change", recipient, previous_key or ""))
        if settings.smtp_enabled and settings.notify_cancellation:
            for key in deleted - set(moved):
                event = old[key]
                if event.starts_at > at:
                    for recipient in self.reminders.sent_recipients(uid(event, "internal")):
                        email_jobs.append(self.reminders.notice(event, "cancel", recipient))
        email_count = sum(
            j.get("status", "pending") == "pending"
            and not self.store.one("SELECT id FROM reminders WHERE dedupe_key=?", (j["dedupe_key"],))
            for j in email_jobs
        )
        email_skipped = sum(
            j.get("status") == "skipped"
            and not self.store.one("SELECT id FROM reminders WHERE dedupe_key=?", (j["dedupe_key"],))
            for j in email_jobs
        )
        deletions = [o for o in operations if o["action"] == "delete"]
        totals, removed = defaultdict(int), defaultdict(int)
        for target, current in remote_maps.items():
            for event_uid, row in current.items():
                key = row.get("source_key") or event_uid.removeprefix(f"exapp-events-{target}-")
                event = old.get(key) or desired.get(key)
                totals[(target, row.get("file_id") or (event.file_id if event else "unknown"))] += 1
        for operation in deletions:
            removed[(operation["target"], operation["file_id"] or "unknown")] += 1
        guarded = settings.delete_guard and (
            len(deletions) > 50
            or any(
                count >= settings.delete_minimum
                and count / max(1, totals[group]) * 100 > settings.delete_percent
                for group, count in removed.items()
            )
        )
        summary = {
            "internal": {
                a: sum(o["target"] == "internal" and o["action"] == a for o in operations)
                for a in ("create", "update", "delete")
            },
            "public": {
                a: sum(o["target"] == "public" and o["action"] == a for o in operations)
                for a in ("create", "update", "delete")
            },
            "email_jobs": email_count,
            "email_skipped": email_skipped,
            "files_scanned": len(sources),
            "issues": issues,
            "operations": operations,
            "requires_confirmation": bool(guarded),
            "root_path": root_path,
            "archive_path": files.archive_path if settings.archive_path else "",
            "archived_files": archived,
            "archive_candidates": sorted(archive_candidates) if settings.archive_path else [],
        }
        # DTSTAMP is volatile; fingerprints bind approval to semantic content, ETags, settings and files.
        fingerprint_ops = [{**o, "ical": canonical_ical(o["ical"]) if o["ical"] else ""} for o in operations]
        summary["fingerprint"] = digest(
            [
                settings.model_dump(),
                sorted(parsed_files, key=lambda f: f["file_id"]),
                sorted(fingerprint_ops, key=lambda o: (o["target"], o["uid"])),
                sorted((k, e.hash(), e.revision) for k, e in desired.items()),
                summary["archive_path"],
                archived,
            ]
        )
        return summary, desired, old, deleted, parsed_files, unavailable, moved, remote_maps

    @staticmethod
    def match_moves(old, desired, deleted):
        # UID changes on time/location edits. Only pair unambiguous rows to avoid sending false change notices.
        def signature(event):
            return event.file_id, event.title, event.responsible

        before, after = defaultdict(list), defaultdict(list)
        for key in deleted:
            before[signature(old[key])].append(key)
        for key in set(desired) - set(old):
            after[signature(desired[key])].append(key)
        return {
            keys[0]: after[sig][0] for sig, keys in before.items() if len(keys) == 1 and len(after[sig]) == 1
        }

    async def run(self, run_id, settings, files, calendars, dry_run=False, confirmation=None):
        async with self.lock:
            self.store.execute("UPDATE sync_runs SET status='running' WHERE id=?", (run_id,))
            try:
                (
                    result,
                    desired,
                    old,
                    deleted,
                    parsed_files,
                    unavailable,
                    moved,
                    remote_maps,
                ) = await self.build(settings, files, calendars)
                status = "dry_run" if dry_run else "completed"
                if confirmation and result["fingerprint"] != confirmation:
                    raise ValueError(
                        "Источник, настройки или календарь изменились; выполните новую проверку перед подтверждением"
                    )
                if not dry_run and result["requires_confirmation"] and not confirmation:
                    status = "requires_confirmation"
                if not dry_run:
                    # The tree can change while other XLSX files / CalDAV are being read.
                    # Refuse a stale plan before committing jobs or deleting generated events.
                    root_path, latest_files = await files.scan(settings)
                    snapshot = sorted((f["file_id"], f["path"], f["etag"]) for f in parsed_files)
                    latest = sorted((f.file_id, f.path, f.etag) for f in latest_files)
                    if root_path != result["root_path"] or snapshot != latest:
                        raise ValueError(
                            "Источник изменился во время сверки; операции отложены до повторной проверки"
                        )
                    if settings.archive_path and (
                        files.archive_path != result["archive_path"]
                        or await files.archived_files(set(result["archive_candidates"]))
                        != result["archived_files"]
                    ):
                        raise ValueError(
                            "Архив изменился во время сверки; операции отложены до повторной проверки"
                        )
                    async with self.reminders.lock:
                        with self.store.transaction():
                            self.store.execute("UPDATE source_files SET status='missing'")
                            for file_id, path in result["archived_files"].items():
                                self.store.execute(
                                    "UPDATE source_files SET path=?,status='archived',last_error='',last_sync_at=? WHERE file_id=?",
                                    (path, now(), file_id),
                                )
                            for file in parsed_files:
                                self.store.execute(
                                    """INSERT INTO source_files VALUES(?,?,?,?,?,?,?,?,?)
                                  ON CONFLICT(file_id) DO UPDATE SET path=excluded.path,etag=excluded.etag,mtime=excluded.mtime,
                                  content_hash=CASE WHEN excluded.content_hash='' THEN source_files.content_hash ELSE excluded.content_hash END,
                                  last_seen_at=excluded.last_seen_at,last_sync_at=excluded.last_sync_at,status=excluded.status,last_error=excluded.last_error""",
                                    (
                                        file["file_id"],
                                        file["path"],
                                        file["etag"],
                                        file["mtime"],
                                        file["content_hash"],
                                        now(),
                                        now(),
                                        file["status"],
                                        file["last_error"],
                                    ),
                                )
                            # Rebuild the cache from actual CalDAV state even if no PUT is necessary.
                            for target, current in remote_maps.items():
                                self.store.execute("DELETE FROM calendar_events WHERE target=?", (target,))
                                for row in current.values():
                                    self.store.execute(
                                        "INSERT INTO calendar_events VALUES(?,?,?,?,?,?,?,?)",
                                        (
                                            target,
                                            row["uid"],
                                            row.get("source_key")
                                            or row["uid"].removeprefix(f"exapp-events-{target}-"),
                                            row["href"],
                                            row["etag"],
                                            row["hash"],
                                            "ok",
                                            "",
                                        ),
                                    )
                            for event in desired.values():
                                self.store.execute(
                                    """INSERT INTO source_events(source_key,file_id,sheet,event_date,start_time,end_time,
                                  location,title,responsible,data_hash,internal_uid,public_uid,public_enabled,revision,data,
                                  created_at,updated_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                                  ON CONFLICT(source_key) DO UPDATE SET sheet=excluded.sheet,event_date=excluded.event_date,
                                  start_time=excluded.start_time,end_time=excluded.end_time,location=excluded.location,title=excluded.title,
                                  responsible=excluded.responsible,data_hash=excluded.data_hash,public_enabled=excluded.public_enabled,
                                  revision=excluded.revision,data=excluded.data,updated_at=excluded.updated_at,last_seen_at=excluded.last_seen_at,active=1""",
                                    (
                                        event.source_key,
                                        event.file_id,
                                        event.sheet,
                                        event.start[:10],
                                        event.start,
                                        event.end,
                                        event.location,
                                        event.title,
                                        event.responsible,
                                        event.hash(),
                                        uid(event, "internal"),
                                        uid(event, "public"),
                                        int(event.public),
                                        event.revision,
                                        dumps(event.data()),
                                        now(),
                                        now(),
                                        now(),
                                    ),
                                )
                            self.reminders.reconcile(list(desired.values()), old, deleted, settings, moved)
                            for key in deleted:
                                # Preserve identity until all independent target deletions can be retried.
                                self.store.execute(
                                    "UPDATE source_events SET active=0 WHERE source_key=?", (key,)
                                )
                    for operation in result["operations"]:
                        # Approval protects Calendar mutations. Validated Excel and the mail queue
                        # are independent, so scheduled notifications continue while approval waits.
                        if status == "requires_confirmation":
                            continue
                        try:
                            await calendars.apply(operation)
                            self.store.execute(
                                "INSERT OR REPLACE INTO calendar_events VALUES(?,?,?,?,?,?,?,?)",
                                (
                                    operation["target"],
                                    operation["uid"],
                                    operation["source_key"],
                                    operation["href"],
                                    "",
                                    canonical_ical(operation["ical"]) if operation["ical"] else "",
                                    "deleted" if operation["action"] == "delete" else "ok",
                                    "",
                                ),
                            )
                            source = self.store.one(
                                "SELECT path FROM source_files WHERE file_id=?", (operation["file_id"],)
                            )
                            self.store.log(
                                "Info",
                                "Calendar",
                                "Событие "
                                + {"create": "создано", "update": "обновлено", "delete": "удалено"}[
                                    operation["action"]
                                ],
                                source["path"] if source else operation["file_id"],
                                operation["sheet"],
                                operation["target"] + ":" + operation["action"],
                            )
                        except Exception:
                            status = "partial"
                            self.store.execute(
                                "INSERT OR REPLACE INTO calendar_events VALUES(?,?,?,?,?,?,?,?)",
                                (
                                    operation["target"],
                                    operation["uid"],
                                    operation["source_key"],
                                    operation["href"],
                                    "",
                                    "",
                                    "retry",
                                    "CalDAV: операция не выполнена",
                                ),
                            )
                            result["issues"].append(
                                {
                                    "level": "Error",
                                    "subsystem": "Calendar",
                                    "file": operation["file_id"],
                                    "sheet": operation["sheet"],
                                    "message": "CalDAV: операция не выполнена; будет повторена",
                                }
                            )
                    if unavailable and status != "requires_confirmation":
                        status = "partial"
                    if status != "requires_confirmation" and any(
                        i["level"] == "Error" for i in result["issues"]
                    ):
                        status = "partial"
                    self.store.set_meta("last_full_reconciliation", now())
                    if status == "completed":
                        self.store.set_meta("last_successful_sync", now())
                    self.store.set_meta("source_path", result["root_path"])
                    self.store.set_meta("archive_path", result["archive_path"])
                    self.store.record_sync_issues(result["issues"])
                self.store.execute(
                    "UPDATE sync_runs SET status=?,finished_at=?,files_scanned=?,events_created=?,events_updated=?,events_deleted=?,warnings=?,errors=?,result=? WHERE id=?",
                    (
                        status,
                        now(),
                        result["files_scanned"],
                        result["internal"]["create"],
                        result["internal"]["update"],
                        result["internal"]["delete"],
                        sum(i["level"] == "Warning" for i in result["issues"]),
                        sum(i["level"] == "Error" for i in result["issues"]),
                        dumps(result),
                        run_id,
                    ),
                )
            except Exception as exc:
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Ошибка сверки; прежнее состояние сохранено. Проверьте конфигурацию."
                )
                self.store.execute(
                    "UPDATE sync_runs SET status='failed',finished_at=?,errors=1,result=? WHERE id=?",
                    (now(), dumps({"message": message}), run_id),
                )
                self.store.log("Error", "Sync", message)

    def create_run(self, kind):
        run_id = uuid4().hex
        self.store.execute(
            "INSERT INTO sync_runs(id,type,started_at,status) VALUES(?,?,?,'queued')", (run_id, kind, now())
        )
        return run_id
