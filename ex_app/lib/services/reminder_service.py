import asyncio
import json
from datetime import UTC, datetime, timedelta

from ..db.store import dumps, now
from ..models import Event, digest
from . import smtp_service
from .calendar_service import uid

BACKOFF = [60, 300, 900, 3600]


class ReminderService:
    def __init__(self, store, delivery=None):
        self.store = store
        self.delivery = delivery
        self.lock = asyncio.Lock()

    def jobs(self, event: Event, settings, at=None):
        at = at or datetime.now(UTC)
        if not settings.smtp_enabled:
            return []
        result = []
        for recipient in event.emails:
            for offset in event.offsets:
                scheduled = event.starts_at.astimezone(UTC) - timedelta(seconds=offset)
                if event.starts_at <= at or scheduled < at - timedelta(minutes=settings.overdue_minutes):
                    continue
                key = digest(
                    [uid(event, "internal"), recipient.casefold(), offset, event.revision, "reminder"]
                )
                result.append(
                    {
                        "event_uid": uid(event, "internal"),
                        "recipient": recipient,
                        "offset_seconds": offset,
                        "scheduled_at": scheduled.isoformat(),
                        "event_revision": event.revision,
                        "dedupe_key": key,
                        "kind": "reminder",
                        "payload": {"kind": "reminder", "event": event.data()},
                    }
                )
        return result

    def queue_job(self, job):
        self.store.execute(
            """INSERT OR IGNORE INTO reminders(event_uid,recipient,offset_seconds,scheduled_at,status,
            event_revision,dedupe_key,kind,payload,created_at,updated_at) VALUES(?,?,?,?,'pending',?,?,?,?,?,?)""",
            (
                job["event_uid"],
                job["recipient"],
                job["offset_seconds"],
                job["scheduled_at"],
                job["event_revision"],
                job["dedupe_key"],
                job["kind"],
                dumps(job["payload"]),
                now(),
                now(),
            ),
        )

    def sent_recipients(self, event_uid):
        return [
            r["recipient"]
            for r in self.store.rows(
                "SELECT DISTINCT recipient FROM reminders WHERE event_uid=? AND status='sent'", (event_uid,)
            )
        ]

    def notice(self, event: Event, kind: str, recipient: str, discriminator=""):
        key = digest([uid(event, "internal"), recipient.casefold(), kind, event.revision, discriminator])
        return {
            "event_uid": uid(event, "internal"),
            "recipient": recipient,
            "offset_seconds": 0,
            "scheduled_at": now(),
            "event_revision": event.revision,
            "dedupe_key": key,
            "kind": kind,
            "payload": {"kind": kind, "event": event.data()},
        }

    def cancel_pending(self, event_uid):
        self.store.execute(
            "UPDATE reminders SET status='cancelled',updated_at=? WHERE event_uid=? AND status IN ('pending','retry')",
            (now(), event_uid),
        )

    def reconcile(self, desired: list[Event], old: dict[str, Event], deleted: set[str], settings, moved=None):
        moved = moved or {}
        with self.store.transaction():
            for event in desired:
                previous = old.get(event.source_key)
                if previous and previous.hash() != event.hash():
                    self.cancel_pending(uid(previous, "internal"))
                    if (
                        settings.smtp_enabled
                        and settings.notify_changes
                        and (previous.start, previous.end, previous.location)
                        != (
                            event.start,
                            event.end,
                            event.location,
                        )
                    ):
                        for recipient in self.sent_recipients(uid(previous, "internal")):
                            self.queue_job(self.notice(event, "change", recipient))
                previous_key = next((k for k, v in moved.items() if v == event.source_key), None)
                if previous_key and settings.smtp_enabled and settings.notify_changes:
                    for recipient in self.sent_recipients(uid(old[previous_key], "internal")):
                        self.queue_job(self.notice(event, "change", recipient, previous_key))
                for job in self.jobs(event, settings):
                    self.queue_job(job)
            for key in deleted:
                event = old.get(key)
                if not event:
                    continue
                self.cancel_pending(uid(event, "internal"))
                if (
                    key not in moved
                    and settings.smtp_enabled
                    and settings.notify_cancellation
                    and event.starts_at > datetime.now(UTC)
                ):
                    for recipient in self.sent_recipients(uid(event, "internal")):
                        self.queue_job(self.notice(event, "cancel", recipient))

    async def tick(self, settings, password):
        if not settings.smtp_enabled:
            return
        async with self.lock:
            for job in self.store.rows(
                "SELECT * FROM reminders WHERE status IN ('pending','retry') AND scheduled_at<=? AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY scheduled_at LIMIT 20",
                (now(), now()),
            ):
                payload = json.loads(job["payload"])
                at = datetime.now(UTC)
                if job["kind"] == "reminder":
                    event = Event(**payload["event"])
                    # Pending old revisions cannot survive a source change. SMTP must never depend on public sync.
                    current = self.store.one(
                        "SELECT revision,active FROM source_events WHERE internal_uid=?", (job["event_uid"],)
                    )
                    if (
                        not current
                        or not current["active"]
                        or current["revision"] != job["event_revision"]
                        or event.starts_at <= at
                    ):
                        self.store.execute("UPDATE reminders SET status='cancelled' WHERE id=?", (job["id"],))
                        continue
                changed = self.store.execute(
                    "UPDATE reminders SET status='sending',attempt_count=attempt_count+1,updated_at=? WHERE id=? AND status IN ('pending','retry')",
                    (now(), job["id"]),
                ).rowcount
                if not changed:
                    continue
                try:
                    if self.delivery:
                        await self.delivery.send(
                            payload, job["recipient"], settings, password, job["dedupe_key"]
                        )
                    else:
                        await asyncio.to_thread(
                            smtp_service.send,
                            payload,
                            job["recipient"],
                            settings,
                            password,
                            job["dedupe_key"],
                        )
                    self.store.execute(
                        "UPDATE reminders SET status='sent',sent_at=?,updated_at=?,last_error=NULL WHERE id=?",
                        (now(), now(), job["id"]),
                    )
                    self.store.log("Info", "Email", "Письмо отправлено", operation=job["kind"])
                except Exception:
                    attempts = job["attempt_count"] + 1
                    state = "failed" if attempts > len(BACKOFF) else "retry"
                    retry = (at + timedelta(seconds=BACKOFF[min(attempts - 1, len(BACKOFF) - 1)])).isoformat()
                    self.store.execute(
                        "UPDATE reminders SET status=?,next_attempt_at=?,last_error='SMTP: ошибка соединения или доставки',updated_at=? WHERE id=?",
                        (state, retry, now(), job["id"]),
                    )
                    self.store.log("Error", "Email", "SMTP: ошибка соединения или доставки; " + state)

    def recover_overdue(self, settings):
        threshold = (datetime.now(UTC) - timedelta(minutes=settings.overdue_minutes)).isoformat()
        self.store.execute(
            "UPDATE reminders SET status='cancelled',updated_at=? WHERE kind='reminder' AND status IN ('pending','retry') AND scheduled_at<?",
            (now(), threshold),
        )
