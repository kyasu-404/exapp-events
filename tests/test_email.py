import json
from datetime import UTC, datetime, timedelta

from ex_app.lib.db.store import Store, now
from ex_app.lib.models import Event
from ex_app.lib.services import smtp_service

from .conftest import ROW, workbook


async def test_queue_restart_and_sent_are_not_repeated(harness, monkeypatch):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    jobs = store.rows("SELECT * FROM reminders")
    store.execute("UPDATE reminders SET status='sent',sent_at=? WHERE id=?", (now(), jobs[0]["id"]))
    reopened = Store(store.path.parent)
    reopened.recover()
    assert reopened.one("SELECT status FROM reminders WHERE id=?", (jobs[0]["id"],))["status"] == "sent"
    assert reopened.one("SELECT status FROM reminders WHERE id=?", (jobs[1]["id"],))["status"] == "pending"
    reopened.close()
    await run()
    assert len(store.rows("SELECT * FROM reminders")) == 2


async def test_send_and_retry_redacts_password(harness, monkeypatch):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    store.execute("UPDATE reminders SET scheduled_at=?", (now(),))

    def fail(*args):
        raise OSError("Secret: smtp-password")

    monkeypatch.setattr(smtp_service, "send", fail)
    await reminders.tick(settings, "smtp-password")
    assert all(j["status"] == "retry" for j in store.rows("SELECT * FROM reminders"))
    assert "smtp-password" not in str(store.rows("SELECT * FROM logs")) + str(
        store.rows("SELECT * FROM reminders")
    )
    store.execute("UPDATE reminders SET next_attempt_at=NULL")
    sent = []
    monkeypatch.setattr(smtp_service, "send", lambda *args: sent.append(args))
    await reminders.tick(settings, "smtp-password")
    assert len(sent) == 2
    await reminders.tick(settings, "smtp-password")
    assert len(sent) == 2


async def test_changes_and_cancellation_only_for_sent_addresses(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    store.execute(
        "UPDATE reminders SET status='sent',sent_at=? WHERE id=(SELECT MIN(id) FROM reminders)", (now(),)
    )
    row = list(ROW)
    row[1] = "16:00"
    files.content["42"] = workbook([row])
    await run()
    change = store.rows("SELECT * FROM reminders WHERE kind='change'")
    assert len(change) == 1 and change[0]["recipient"] == "school@example.org"
    assert not store.rows("SELECT * FROM reminders WHERE kind='cancel'")
    store.execute("UPDATE reminders SET status='sent' WHERE kind='change'")
    files.content["42"] = workbook([])
    await run()
    assert len(store.rows("SELECT * FROM reminders WHERE kind='cancel'")) == 1
    await run()
    assert len(store.rows("SELECT * FROM reminders WHERE kind='cancel'")) == 1


async def test_title_edit_no_change_mail_and_revision(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    store.execute("UPDATE reminders SET status='sent',sent_at=?", (now(),))
    row = list(ROW)
    row[2] = "Название изменено"
    files.content["42"] = workbook([row])
    await run()
    assert not store.rows("SELECT * FROM reminders WHERE kind='change'")
    assert store.one("SELECT revision FROM source_events")["revision"] == 2


async def test_overdue_recovery_and_ambiguous_sending(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    store.execute(
        "UPDATE reminders SET scheduled_at=?", ((datetime.now(UTC) - timedelta(minutes=10)).isoformat(),)
    )
    reminders.recover_overdue(settings)
    assert all(j["status"] == "cancelled" for j in store.rows("SELECT * FROM reminders"))
    store.execute("UPDATE reminders SET status='sending'")
    store.recover()
    assert all(j["status"] == "failed" for j in store.rows("SELECT * FROM reminders"))


async def test_email_html_escaped_and_plain_fallback(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    event = Event(**json.loads(store.one("SELECT data FROM source_events")["data"]))
    event.title = '<script>alert("a")</script>'
    mail = smtp_service.message(
        {"event": event.data(), "kind": "reminder"}, "someone@example.org", settings, "message"
    )
    assert mail.is_multipart()
    assert "<script>" not in mail.get_body(preferencelist=("html",)).get_content()
    assert "<script>" in mail.get_body(preferencelist=("plain",)).get_content()
    assert mail["To"] == "someone@example.org" and "ATTENDEE" not in mail.as_string()


def test_atomic_rollback(harness):
    store, *_ = harness
    try:
        with store.transaction():
            store.set_meta("atomic", "1")
            raise ValueError("rollback")
    except ValueError:
        pass
    assert store.meta("atomic") == ""


async def test_manual_retry_cannot_remind_cancelled_event(harness, monkeypatch):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    store.execute("UPDATE reminders SET status='failed',scheduled_at=?", (now(),))
    files.content["42"] = workbook([])
    await run()
    store.execute("UPDATE reminders SET status='retry',next_attempt_at=NULL")
    sent = []
    monkeypatch.setattr(smtp_service, "send", lambda *args: sent.append(args))
    await reminders.tick(settings, "password")
    assert not sent
    assert all(row["status"] == "cancelled" for row in store.rows("SELECT status FROM reminders"))


async def test_smtp_disabled_does_not_queue_change_or_cancellation(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    store.execute("UPDATE reminders SET status='sent',sent_at=?", (now(),))
    settings.smtp_enabled = False
    row = list(ROW)
    row[1] = "16:00"
    files.content["42"] = workbook([row])
    await run()
    files.content["42"] = workbook([])
    await run()
    assert not store.rows("SELECT id FROM reminders WHERE kind IN ('change','cancel')")
