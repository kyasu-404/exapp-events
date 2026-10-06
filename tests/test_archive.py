from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ex_app.lib.models import Settings
from ex_app.lib.services.event_listener import enqueue
from ex_app.lib.services.nextcloud_files import NextcloudFiles, SourceFile, within_folder

from .conftest import ROW, workbook
from .test_adapters import node


@pytest.mark.parametrize("path", ["Events/Archive/old.xlsx", "/Events/Archive/2026/old.xlsx"])
def test_archive_path_handles_leading_slash_and_subfolders(path):
    assert within_folder(path, "/Events/Archive/")
    assert not within_folder("/Events/Archive-old/file.xlsx", "/Events/Archive")


async def test_archive_not_listed_and_renamed_folder_excluded_by_id():
    root = node(7, "/Events", True)
    archive = node(8, "/Events/Archive", True)
    nc = SimpleNamespace(
        files=SimpleNamespace(
            by_id=AsyncMock(side_effect=[root, archive]),
            listdir=AsyncMock(
                return_value=[node(8, "/Events/Renamed", True), node(42, "/Events/current.xlsx")]
            ),
        )
    )
    files = NextcloudFiles(nc)
    _, source = await files.scan(Settings(source_id="7", archive_id="8", archive_path="/Events/Archive"))
    assert [f.file_id for f in source] == ["42"]
    nc.files.listdir.assert_awaited_once_with(root)


async def test_archive_metadata_resolution_never_downloads_workbooks():
    nc = SimpleNamespace(
        files=SimpleNamespace(
            by_id=AsyncMock(side_effect=[node(42, "/Events/Archive/2026/plan.xlsx"), None]),
            download=AsyncMock(),
            listdir=AsyncMock(),
        )
    )
    files = NextcloudFiles(nc)
    files.archive_path = "/Events/Archive"
    assert await files.archived_files({"42", "43"}) == {"42": "/Events/Archive/2026/plan.xlsx"}
    nc.files.download.assert_not_called()
    nc.files.listdir.assert_not_called()
    nc.files.by_id.return_value = node(42, "/Events/Archive/2026/plan.xlsx")
    nc.files.by_id.side_effect = None
    with pytest.raises(ValueError, match="архив"):
        await files.read(SourceFile("42", "/Events/plan.xlsx"))
    nc.files.download.assert_not_called()


@pytest.mark.parametrize("archive", ["/Events", "/", ""])
async def test_archive_cannot_exclude_source_itself(archive):
    root = node(7, "/Events", True)
    nc = SimpleNamespace(files=SimpleNamespace(by_path=AsyncMock(return_value=node(8, archive, True))))
    if archive:
        with pytest.raises(ValueError, match="источником"):
            await NextcloudFiles(nc).resolve_archive(Settings(archive_path=archive), root)
    else:
        assert await NextcloudFiles(nc).resolve_archive(Settings(), root) is None
        nc.files.by_path.assert_not_called()


async def test_preview_rejects_archived_workbook():
    root, archive = node(7, "/Events", True), node(8, "/Events/Archive", True)
    nc = SimpleNamespace(
        files=SimpleNamespace(
            by_id=AsyncMock(side_effect=[root, archive, node(42, "/Events/Archive/plan.xlsx")])
        )
    )
    with pytest.raises(ValueError, match="архивной"):
        await NextcloudFiles(nc).preview_file(
            "42", Settings(source_id="7", archive_id="8", archive_path=archive.user_path)
        )


async def test_unavailable_archive_aborts_scan_before_listing_source():
    nc = SimpleNamespace(
        files=SimpleNamespace(
            by_id=AsyncMock(side_effect=[node(7, "/Events", True), None]), listdir=AsyncMock()
        )
    )
    with pytest.raises(ValueError, match="недоступна"):
        await NextcloudFiles(nc).scan(Settings(source_id="7", archive_path="/Events/Archive", archive_id="8"))
    nc.files.listdir.assert_not_called()


@pytest.mark.parametrize("policy", ["future", "keep", "all"])
async def test_archiving_removes_past_and_future_events_from_both_calendars(harness, policy):
    store, reminders, engine, settings, files, calendars, run = harness
    settings.delete_guard = False
    settings.missing_file_policy = policy
    past = list(ROW)
    past[0] = "Прошедшее"
    files.files["43"] = SourceFile("43", "/2025/Сентябрь/past.xlsx")
    files.content["43"] = workbook([past])
    await run()
    assert len(calendars.events["internal"]) == 2
    settings.archive_path = "/Archive"
    files.archive = {"42": "/Archive/future.xlsx", "43": "/Archive/past.xlsx"}
    files.files.clear()
    calendars.writes.clear()
    result = await run()
    assert result["status"] == "completed", result["result"]
    assert len(calendars.writes) == 4 and all(o["action"] == "delete" for o in calendars.writes)
    assert not calendars.events["internal"] and not calendars.events["public"]
    assert not store.rows("SELECT id FROM reminders WHERE status IN ('pending','retry')")
    assert all(r["status"] == "archived" for r in store.rows("SELECT status FROM source_files"))
    assert not store.rows("SELECT source_key FROM source_events WHERE active=1")
    calendars.writes.clear()
    await run()
    assert not calendars.writes


async def test_archived_deletion_retries_failed_target(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    settings.archive_path = "/Archive"
    files.archive = {"42": "/Archive/plan.xlsx"}
    files.files.clear()
    calendars.fail_apply.add("public")
    assert (await run())["status"] == "partial"
    assert calendars.events["public"] and not calendars.events["internal"]
    calendars.fail_apply.clear()
    assert (await run())["status"] == "completed"
    assert not calendars.events["public"]


async def test_return_from_archive_reactivates_event_and_reminders(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    original_file = files.files["42"]
    settings.archive_path = "/Archive"
    files.archive = {"42": "/Archive/plan.xlsx"}
    files.files.clear()
    await run()
    files.archive.clear()
    files.files["42"] = original_file
    await run()
    assert store.one("SELECT revision,active FROM source_events") == {"revision": 2, "active": 1}
    assert len(calendars.events["internal"]) == 1
    assert len(store.rows("SELECT id FROM reminders WHERE status='pending'")) == 2


async def test_archive_move_race_defers_calendar_deletion(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    settings.archive_path = "/Archive"
    files.archive = {"42": "/Archive/plan.xlsx"}
    files.files.clear()
    original = files.archived_files
    calls = 0

    async def changing_archive(ids):
        nonlocal calls
        calls += 1
        if calls == 3:
            files.archive.clear()
        return await original(ids)

    files.archived_files = changing_archive
    calendars.writes.clear()
    assert (await run())["status"] == "failed"
    assert not calendars.writes
    assert store.one("SELECT active FROM source_events")["active"] == 1


def test_archive_webhooks_are_ignored_but_move_into_archive_reconciles(harness):
    store, reminders, engine, settings, *_ = harness
    settings.source_path = "/Events"
    settings.archive_path = "/Events/Archive"
    event = {
        "class": "OCP\\Files\\Events\\Node\\NodeWrittenEvent",
        "node": {"id": 42, "path": "/admin/files/Events/Archive/plan.xlsx"},
    }
    assert not enqueue(store, {"event": event}, settings)
    event = {
        "class": "OCP\\Files\\Events\\Node\\NodeRenamedEvent",
        "source": {"id": 42, "path": "/admin/files/Events/plan.xlsx"},
        "target": {"id": 42, "path": "/admin/files/Events/Archive/plan.xlsx"},
    }
    assert enqueue(store, {"event": event}, settings)


async def test_disabling_confirmation_applies_pending_calendar_plan(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    settings.delete_minimum = 1
    files.content["42"] = workbook([])
    assert (await run())["status"] == "requires_confirmation"
    settings.delete_guard = False
    assert (await run())["status"] == "completed"
    assert not calendars.events["internal"] and not calendars.events["public"]


async def test_archive_cleanup_recovers_file_identity_from_calendar_metadata(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    files.files["43"] = SourceFile("43", "/2025/Сентябрь/past.xlsx")
    files.content["43"] = workbook([ROW])
    await run()
    store.execute("DELETE FROM source_events")
    store.execute("DELETE FROM source_files")
    settings.archive_path = "/Archive"
    files.archive = {"42": "/Archive/future.xlsx", "43": "/Archive/past.xlsx"}
    files.files.clear()
    result = await run()
    assert result["status"] == "completed", result["result"]
    assert not calendars.events["internal"] and not calendars.events["public"]
