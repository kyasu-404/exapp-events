import json
from datetime import UTC, datetime, timedelta

import pytest

from ex_app.lib.models import Event, Settings
from ex_app.lib.services import smtp_service
from ex_app.lib.services.event_listener import enqueue
from ex_app.lib.services.nextcloud_files import SourceFile

from .conftest import ROW, workbook


async def test_calendar_confirmation_does_not_block_new_reminder_and_survives_retry(harness, monkeypatch):
    store, reminders, engine, settings, files, calendars, run = harness
    settings.delete_minimum = 1
    await run()
    store.execute("DELETE FROM reminders")
    # Replace the original XLSX, as happened in production: new file ID, same path.
    files.files = {"43": SourceFile("43", files.files["42"].path, "e2")}
    files.content["43"] = workbook([ROW])
    calendars.writes.clear()
    result = await run()
    assert result["status"] == "requires_confirmation"
    assert not calendars.writes
    jobs = store.rows("SELECT * FROM reminders")
    assert len(jobs) == 2 and all(j["status"] == "pending" for j in jobs)
    assert store.one("SELECT active FROM source_events WHERE file_id='42'")["active"] == 0
    assert store.one("SELECT active FROM source_events WHERE file_id='43'")["active"] == 1
    fingerprint = json.loads(result["result"])["fingerprint"]
    # Periodic reconciliation must not duplicate jobs or invalidate the approval.
    again = await run()
    assert json.loads(again["result"])["fingerprint"] == fingerprint
    assert len(store.rows("SELECT id FROM reminders")) == 2
    sent = []
    monkeypatch.setattr(smtp_service, "send", lambda *args: sent.append(args))
    store.execute("UPDATE reminders SET scheduled_at=?", (datetime.now(UTC).isoformat(),))
    await reminders.tick(settings, "")
    await reminders.tick(settings, "")
    assert len(sent) == 2
    assert all(r["status"] == "sent" for r in store.rows("SELECT status FROM reminders"))
    assert (await run(confirmation=fingerprint))["status"] == "completed"
    assert len(store.rows("SELECT id FROM reminders")) == 2


async def test_guard_still_rejects_source_race_before_mail_queue(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    settings.delete_minimum = 1
    await run()
    store.execute("DELETE FROM reminders")
    files.files = {"43": SourceFile("43", "/2027/Сентябрь/replaced.xlsx", "e2")}
    files.content["43"] = workbook([ROW])
    scan = files.scan
    calls = 0

    async def changing_scan(settings):
        nonlocal calls
        calls += 1
        if calls == 2:
            files.files["43"].etag = "changed"
        return await scan(settings)

    files.scan = changing_scan
    assert (await run())["status"] == "failed"
    assert not store.rows("SELECT id FROM reminders")
    assert store.one("SELECT active FROM source_events WHERE file_id='42'")["active"] == 1


async def test_identical_issues_logged_once_and_again_after_resolution(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    row = list(ROW)
    row[3] = None
    files.content["42"] = workbook([row])
    await run()
    await run()
    logs = store.rows("SELECT * FROM logs WHERE level='Warning'")
    assert len(logs) == 1 and logs[0]["operation"] == "Строка 2"
    assert "Ответственный" in logs[0]["result"]
    assert len(json.loads(store.meta("sync_issues"))) == 1
    files.content["42"] = workbook([ROW])
    await run()
    assert json.loads(store.meta("sync_issues")) == []
    files.content["42"] = workbook([row])
    await run()
    assert len(store.rows("SELECT * FROM logs WHERE level='Warning'")) == 2


@pytest.mark.parametrize("root", ["0. Планы ИМЦ/", "/0. Планы ИМЦ/", "/"])
def test_new_file_webhook_matches_folder_with_or_without_leading_slash(harness, root):
    store, *_ = harness
    settings = Settings(source_path=root)
    assert enqueue(
        store,
        {
            "event": {
                "class": "OCP\\Files\\Events\\Node\\NodeWrittenEvent",
                "node": {"id": 900, "path": "/admin/files/0. Планы ИМЦ/2026/Октябрь/Октябрь.xlsx"},
            }
        },
        settings,
    )
    assert store.one("SELECT file_id FROM event_queue")["file_id"] == "900"


async def test_expired_unqueued_reminder_is_visible_and_never_sent(harness, monkeypatch):
    store, reminders, engine, settings, files, calendars, run = harness
    at = datetime.now(UTC)
    event = Event(
        "expired",
        "42",
        "06.10",
        (at + timedelta(minutes=30)).isoformat(),
        (at + timedelta(minutes=90)).isoformat(),
        "Зал",
        "Проверка",
        "Иванова",
        ["school@example.org"],
        [7200],
    )
    job = reminders.jobs(event, settings, at)[0]
    assert job["status"] == "skipped"
    reminders.queue_job(job)
    reminders.queue_job(job)
    sent = []
    monkeypatch.setattr(smtp_service, "send", lambda *args: sent.append(args))
    await reminders.tick(settings, "")
    assert not sent
    assert store.one("SELECT status,last_error FROM reminders")["status"] == "skipped"
    assert len(store.rows("SELECT id FROM reminders")) == 1
    settings.overdue_minutes = 120
    assert reminders.jobs(event, settings, at)[0]["status"] == "pending"
