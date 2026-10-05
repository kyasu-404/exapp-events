from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from ex_app.lib.models import Settings
from ex_app.lib.services.calendar_service import C, CalendarService, D, DavError, event_ical, uid
from ex_app.lib.services.nextcloud_files import NextcloudFiles, SourceFile


def node(file_id, path, directory=False, etag="v1"):
    return SimpleNamespace(
        file_id=f"{file_id:08d}instance",
        user_path=path,
        name=path.rsplit("/", 1)[-1],
        is_dir=directory,
        etag=etag,
        info=SimpleNamespace(fileid=file_id, last_modified=datetime.now(UTC), content_length=10),
    )


async def test_files_scan_uses_numeric_event_ids_and_filters():
    root, nested = node(7, "/Events", True), node(8, "/Events/2027", True)
    nc = SimpleNamespace(
        files=SimpleNamespace(
            by_id=AsyncMock(return_value=root),
            listdir=AsyncMock(
                side_effect=[
                    [nested, node(40, "/Events/~$lock.xlsx")],
                    [node(42, "/Events/2027/PLAN.XLSX"), node(43, "/Events/2027/a.csv")],
                ]
            ),
        )
    )
    path, sources = await NextcloudFiles(nc).scan(Settings(source_id="7"))
    assert path == "/Events" and [s.file_id for s in sources] == ["42"]
    nc.files.by_id.assert_awaited_once_with("7")


async def test_files_download_rejects_concurrent_save():
    nc = SimpleNamespace(
        files=SimpleNamespace(
            by_id=AsyncMock(side_effect=[node(42, "/Events/a.xlsx"), node(42, "/Events/a.xlsx", etag="v2")]),
            download=AsyncMock(return_value=b"content"),
        )
    )
    with pytest.raises(ValueError, match="изменился"):
        await NextcloudFiles(nc).read(SourceFile("42", "/Events/a.xlsx"))


@pytest.fixture
def calendar():
    transport = SimpleNamespace(request=AsyncMock())
    session = SimpleNamespace(init_adapter_dav=Mock(), adapter_dav=transport)
    nc = SimpleNamespace(
        _session=session,
        app_cfg=SimpleNamespace(
            endpoint="https://nextcloud.example/",
            dav_endpoint="https://nextcloud.example/remote.php/dav/",
        ),
    )
    return CalendarService(nc, "admin"), transport


@pytest.mark.parametrize(
    "href",
    [
        "https://evil.example/remote.php/dav/calendars/admin/events/",
        "calendars/admin/%2e%2e/other/",
        "calendars/user/events/",
        "calendars/admin/events/?secret=token",
    ],
)
def test_dav_cannot_escape_owner_home(calendar, href):
    service, transport = calendar
    with pytest.raises(ValueError):
        service.safe_path(href)


async def test_caldav_multistatus_and_conditional_writes(calendar, harness):
    service, transport = calendar
    store, reminders, engine, settings, files, calendars, run = harness
    await run()
    import json

    from ex_app.lib.models import Event

    event = Event(**json.loads(store.one("SELECT data FROM source_events")["data"]))
    root = Element(f"{{{D}}}multistatus")
    response = SubElement(root, f"{{{D}}}response")
    SubElement(response, f"{{{D}}}href").text = "/remote.php/dav/calendars/admin/internal/event.ics"
    block = SubElement(response, f"{{{D}}}propstat")
    SubElement(block, f"{{{D}}}status").text = "HTTP/1.1 200 OK"
    props = SubElement(block, f"{{{D}}}prop")
    SubElement(props, f"{{{D}}}getetag").text = '"current"'
    SubElement(props, f"{{{C}}}calendar-data").text = event_ical(event, "internal").decode()
    transport.request.return_value = SimpleNamespace(status_code=207, content=tostring(root))
    actual = await service.current("calendars/admin/internal/", "internal")
    assert actual[uid(event, "internal")]["file_id"] == "42"
    assert actual[uid(event, "internal")]["etag"] == '"current"'
    transport.request.return_value = SimpleNamespace(status_code=204)
    await service.apply(
        {
            "action": "update",
            "href": actual[uid(event, "internal")]["href"],
            "etag": '"current"',
            "ical": event_ical(event, "internal").decode(),
        }
    )
    assert transport.request.call_args.kwargs["headers"]["If-Match"] == '"current"'
    transport.request.return_value = SimpleNamespace(status_code=412)
    with pytest.raises(DavError):
        await service.apply(
            {
                "action": "create",
                "href": "calendars/admin/internal/new.ics",
                "etag": "",
                "ical": event_ical(event, "internal").decode(),
            }
        )
    assert transport.request.call_args.kwargs["headers"]["If-None-Match"] == "*"
