import asyncio
import json

import pytest

from .conftest import ROW, workbook


async def test_repeat_and_dry_run(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    before = store.rows("SELECT * FROM source_files")
    result = await run(True)
    assert result["status"] == "dry_run"
    assert not calendars.writes and not store.rows("SELECT * FROM reminders")
    assert store.rows("SELECT * FROM source_files") == before
    assert json.loads(result["result"])["email_jobs"] == 2
    first = await run()
    assert first["status"] == "completed", first["result"]
    assert len(calendars.writes) == 2 and len(store.rows("SELECT * FROM reminders")) == 2
    await run()
    assert len(calendars.writes) == 2 and len(store.rows("SELECT * FROM reminders")) == 2


@pytest.mark.parametrize(
    "index,value,actions",
    [
        (2, "Новое название", ["update", "update"]),
        (3, "Новый ответственный", ["update"]),
        (1, "16:00", ["create", "delete", "create", "delete"]),
        (0, "Другое место", ["create", "delete", "create", "delete"]),
    ],
)
async def test_edits(harness, index, value, actions):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    calendars.writes.clear()
    row = list(ROW)
    row[index] = value
    files.content["42"] = workbook([row])
    result = await run()
    assert result["status"] == "completed", result["result"]
    assert sorted(o["action"] for o in calendars.writes) == sorted(actions)
    assert len(calendars.events["internal"]) == 1


async def test_reorder_rename_and_move(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    second = list(ROW)
    second[0] = "10 кабинет"
    files.content["42"] = workbook([ROW, second])
    await run()
    calendars.writes.clear()
    files.content["42"] = workbook([second, ROW])
    files.files["42"].path = "/2027/Extra/Сентябрь/Renamed.xlsx"
    await run()
    assert not calendars.writes
    assert store.one("SELECT path FROM source_files")["path"].endswith("Renamed.xlsx")


async def test_public_privacy_and_toggle(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    public = next(v for k, v in calendars.contents.items() if "public" in k)
    assert (
        "Иванова" not in public
        and "example.org" not in public
        and "SOURCE" not in public
        and "ATTENDEE" not in public
    )
    row = list(ROW)
    row[6] = "Нет"
    files.content["42"] = workbook([row])
    calendars.writes.clear()
    await run()
    assert len(calendars.events["internal"]) == 1 and not calendars.events["public"]
    assert [o["target"] for o in calendars.writes] == ["public"]
    files.content["42"] = workbook([ROW])
    await run()
    assert len(calendars.events["public"]) == 1


async def test_bad_file_never_means_empty(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    calendars.writes.clear()
    for content in [b"broken", OSError(), workbook([ROW, ROW]), workbook([ROW], "15.10")]:
        files.content["42"] = content
        await run()
        assert not calendars.writes and len(calendars.events["internal"]) == 1
    files.fail = True
    assert (await run())["status"] == "failed"
    assert not calendars.writes


async def test_delete_future_and_keep_history(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    past = list(ROW)
    past[0] = "Прошедшее"
    from ex_app.lib.services.nextcloud_files import SourceFile

    files.files["43"] = SourceFile("43", "/2025/Сентябрь/old.xlsx")
    files.content["43"] = workbook([past])
    await run()
    files.files.clear()
    await run()
    assert len(calendars.events["internal"]) == 1
    assert "Прошедшее" in next(iter(calendars.contents.values()))
    assert len(store.rows("SELECT id FROM reminders WHERE status='cancelled'")) == 2
    assert len(store.rows("SELECT id FROM reminders WHERE status='skipped'")) == 2


async def test_full_reconciliation_restores_calendar(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    event_uid = next(iter(calendars.events["internal"]))
    calendars.events["internal"][event_uid]["hash"] = "manual-edit"
    calendars.writes.clear()
    await run()
    assert calendars.writes[0]["action"] == "update"
    calendars.events["internal"].clear()
    await run()
    assert len(calendars.events["internal"]) == 1


async def test_independent_targets_and_deleted_retry(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    calendars.fail.add("public")
    assert (await run())["status"] == "partial"
    assert len(calendars.events["internal"]) == 1 and len(store.rows("SELECT * FROM reminders")) == 2
    calendars.fail.clear()
    await run()
    assert len(calendars.events["public"]) == 1
    files.content["42"] = workbook([])
    calendars.fail_apply.add("public")
    await run()
    assert not calendars.events["internal"] and calendars.events["public"]
    calendars.fail_apply.clear()
    await run()
    assert not calendars.events["public"]


async def test_guard_and_stale_confirmation(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    settings.delete_minimum = 1
    await run()
    files.content["42"] = workbook([])
    calendars.writes.clear()
    row = await run()
    assert row["status"] == "requires_confirmation"
    assert not calendars.writes
    fingerprint = json.loads(row["result"])["fingerprint"]
    files.files["42"].etag = "changed"
    assert (await run(confirmation=fingerprint))["status"] == "failed"
    assert not calendars.writes
    row = await run()
    assert (await run(confirmation=json.loads(row["result"])["fingerprint"]))["status"] == "completed"
    assert not calendars.events["internal"]


async def test_concurrent_runs_idempotent(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await asyncio.gather(run(), run(), run())
    assert len(calendars.events["internal"]) == 1 and len(calendars.writes) == 2
    assert len(store.rows("SELECT * FROM reminders")) == 2


async def test_recovery_without_source_state(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    store.execute("DELETE FROM source_events")
    store.execute("DELETE FROM calendar_events")
    calendars.writes.clear()
    await run()
    assert not calendars.writes
    assert len(store.rows("SELECT * FROM calendar_events WHERE status='ok'")) == 2
    files.content["42"] = workbook([])
    await run()
    assert not calendars.events["internal"] and not calendars.events["public"]


async def test_missing_files_do_not_stay_in_overview(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    files.files.clear()
    await run()
    assert store.one("SELECT status FROM source_files")["status"] == "missing"


async def test_source_change_after_plan_blocks_all_mutations(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    files.content["42"] = workbook([])
    calendars.writes.clear()
    scan = files.scan
    calls = 0

    async def changing_scan(settings):
        nonlocal calls
        calls += 1
        if calls == 2:
            files.files["42"].etag = "saved-during-reconciliation"
        return await scan(settings)

    files.scan = changing_scan
    row = await run()
    assert row["status"] == "failed" and not calendars.writes
    assert store.one("SELECT active FROM source_events")["active"] == 1
    assert not store.rows("SELECT id FROM reminders WHERE status='cancelled'")
