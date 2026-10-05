import copy
from io import BytesIO

import pytest
from icalendar import Calendar
from openpyxl import Workbook

from ex_app.lib.db.store import Store
from ex_app.lib.models import HEADERS, Settings
from ex_app.lib.services.calendar_service import canonical_ical
from ex_app.lib.services.nextcloud_files import SourceFile
from ex_app.lib.services.reminder_service import ReminderService
from ex_app.lib.services.sync_engine import SyncEngine


def workbook(rows=None, sheet="21.09", headers=None):
    book = Workbook()
    book.active.title = sheet
    book.active.append(headers or list(HEADERS.values()))
    for row in rows or []:
        book.active.append(row)
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


ROW = ["9 кабинет", "15:30", "Совещание", "Иванова И.И.", "school@example.org", "24ч;2ч", "Да"]


class FakeFiles:
    def __init__(self, content=None):
        self.files = {"42": SourceFile("42", "/2027/Сентябрь/Сетка.xlsx", "e1")}
        self.content = {"42": content or workbook([ROW])}
        self.fail = False

    async def scan(self, settings):
        if self.fail:
            raise ValueError("Папка недоступна")
        return "/", list(copy.deepcopy(self.files).values())

    async def read(self, file):
        if isinstance(self.content[file.file_id], Exception):
            raise self.content[file.file_id]
        return self.content[file.file_id]


class FakeCalendars:
    def __init__(self):
        self.events = {"internal": {}, "public": {}}
        self.contents = {}
        self.writes = []
        self.fail = set()
        self.fail_apply = set()

    async def current(self, url, target):
        if target in self.fail:
            raise OSError("down")
        return copy.deepcopy(self.events[target])

    async def apply(self, operation):
        target, key = operation["target"], operation["uid"]
        if target in self.fail_apply:
            raise OSError("down")
        self.writes.append(copy.deepcopy(operation))
        if operation["action"] == "delete":
            self.events[target].pop(key, None)
            self.contents.pop(key, None)
        else:
            item = Calendar.from_ical(operation["ical"]).walk("VEVENT")[0]
            self.events[target][key] = {
                "uid": key,
                "href": operation["href"],
                "etag": "e" + str(len(self.writes)),
                "hash": canonical_ical(operation["ical"]),
                "file_id": str(item.get("X-EXAPP-EVENTS-SOURCE-FILE-ID", "")),
                "source_key": str(item.get("X-EXAPP-EVENTS-SOURCE-KEY", "")),
                "start": item.decoded("DTSTART").isoformat(),
            }
            self.contents[key] = operation["ical"]


@pytest.fixture
def harness(tmp_path):
    store = Store(tmp_path)
    reminders = ReminderService(store)
    engine = SyncEngine(store, reminders)
    settings = Settings(
        source_owner="admin",
        source_id="7",
        source_path="/",
        calendar_owner="admin",
        internal_calendar="calendars/admin/internal/",
        public_calendar="calendars/admin/public/",
        public_enabled=True,
        smtp_enabled=True,
        smtp_sender="events@example.org",
        smtp_host="smtp.example.org",
    )
    files, calendars = FakeFiles(), FakeCalendars()

    async def run(dry=False, confirmation=None):
        run_id = engine.create_run("dry_run" if dry else "test")
        await engine.run(run_id, settings, files, calendars, dry, confirmation)
        return store.one("SELECT * FROM sync_runs WHERE id=?", (run_id,))

    yield store, reminders, engine, settings, files, calendars, run
    store.close()
