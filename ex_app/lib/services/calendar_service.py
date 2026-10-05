from datetime import UTC, datetime
from urllib.parse import quote, unquote, urlparse
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml.ElementTree import fromstring
from icalendar import Calendar
from icalendar import Event as IEvent

from ..models import Event, digest

D = "DAV:"
C = "urn:ietf:params:xml:ns:caldav"


def uid(event: Event, target: str) -> str:
    return f"exapp-events-{target}-{event.source_key}"


def event_ical(event: Event, target: str) -> bytes:
    calendar, item = Calendar(), IEvent()
    calendar.add("prodid", "-//exapp-events//Мероприятия//RU")
    calendar.add("version", "2.0")
    item.add("uid", uid(event, target))
    item.add("dtstamp", datetime.now(UTC))
    # UTC values avoid dependence on container TZ or missing VTIMEZONE definitions.
    item.add("dtstart", datetime.fromisoformat(event.start).astimezone(UTC))
    item.add("dtend", datetime.fromisoformat(event.end).astimezone(UTC))
    item.add("summary", event.title)
    item.add("location", event.location)
    if target == "internal":
        item.add("description", f"Ответственный: {event.responsible}")
        item.add("X-EXAPP-EVENTS", "1")
        item.add("X-EXAPP-EVENTS-SOURCE-FILE-ID", event.file_id)
        item.add("X-EXAPP-EVENTS-SOURCE-SHEET", event.sheet)
        item.add("X-EXAPP-EVENTS-SOURCE-KEY", event.source_key)
    calendar.add_component(item)
    return calendar.to_ical()


def canonical_ical(content: bytes | str) -> str:
    item = next(c for c in Calendar.from_ical(content).walk() if c.name == "VEVENT")
    # Include every content property except volatile bookkeeping: manual edits are reverted.
    return digest(
        sorted(
            (str(k), v.to_ical().decode() if hasattr(v, "to_ical") else str(v))
            for k, v in item.items()
            if k not in {"DTSTAMP", "CREATED", "LAST-MODIFIED", "SEQUENCE"}
        )
    )


class DavError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"CalDAV HTTP {status}")


class CalendarService:
    """CalDAV via nc_py_api's authenticated DAV transport, isolated from other services.

    nc_py_api 0.30.3 exposes no async Calendar wrapper. Its synchronous wrapper uses
    the same transport. RFC 4791/4918 operations below do not use Calendar's private REST APIs.
    """

    def __init__(self, nc, owner: str):
        self.nc, self.owner = nc, owner
        self.home = f"calendars/{quote(owner, safe='')}/"

    def safe_path(self, href: str) -> str:
        parsed = urlparse(href)
        endpoint = urlparse(self.nc.app_cfg.endpoint)
        if parsed.netloc and (parsed.netloc != endpoint.netloc or parsed.scheme != endpoint.scheme):
            raise ValueError("CalDAV URL должен принадлежать этому Nextcloud")
        path = parsed.path
        prefix = urlparse(self.nc.app_cfg.dav_endpoint).path.rstrip("/") + "/"
        if path.startswith(prefix):
            path = path[len(prefix) :]
        path = path.lstrip("/")
        if (
            not path.startswith(self.home)
            or any(p in {".", ".."} for p in unquote(path).split("/"))
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Недопустимый путь календаря")
        return path

    async def request(self, method: str, path: str, body=b"", headers=None):
        # This is the only access to the SDK transport implementation.
        session = self.nc._session
        session.init_adapter_dav()
        response = await session.adapter_dav.request(method, path, data=body, headers=headers or {})
        if response.status_code >= 400:
            raise DavError(response.status_code)
        return response

    async def list_calendars(self):
        root = Element(f"{{{D}}}propfind")
        prop = SubElement(root, f"{{{D}}}prop")
        for ns, name in [
            (D, "displayname"),
            (D, "resourcetype"),
            (D, "current-user-privilege-set"),
            (C, "supported-calendar-component-set"),
        ]:
            SubElement(prop, f"{{{ns}}}{name}")
        response = await self.request(
            "PROPFIND", self.home, tostring(root), {"Depth": "1", "Content-Type": "application/xml"}
        )
        result = []
        for item in fromstring(response.content).findall(f"{{{D}}}response"):
            for block in item.findall(f"{{{D}}}propstat"):
                if " 200 " not in block.findtext(f"{{{D}}}status", ""):
                    continue
                props = block.find(f"{{{D}}}prop")
                if props.find(f".//{{{C}}}calendar") is not None:
                    writable = (
                        props.find(f".//{{{D}}}write") is not None
                        or props.find(f".//{{{D}}}write-content") is not None
                    )
                    result.append(
                        {
                            "url": self.safe_path(item.findtext(f"{{{D}}}href")),
                            "name": props.findtext(f"{{{D}}}displayname", "Календарь"),
                            "writable": writable,
                        }
                    )
        return result

    async def create(self, name: str):
        from uuid import uuid4

        path = self.home + "events-" + uuid4().hex + "/"
        root = Element(f"{{{C}}}mkcalendar")
        props = SubElement(SubElement(root, f"{{{D}}}set"), f"{{{D}}}prop")
        SubElement(props, f"{{{D}}}displayname").text = name
        components = SubElement(props, f"{{{C}}}supported-calendar-component-set")
        SubElement(components, f"{{{C}}}comp", {"name": "VEVENT"})
        await self.request("MKCALENDAR", path, tostring(root), {"Content-Type": "application/xml"})
        return {"url": path, "name": name, "writable": True}

    async def current(self, calendar_url: str, target: str):
        path = self.safe_path(calendar_url)
        root = Element(f"{{{C}}}calendar-query")
        props = SubElement(root, f"{{{D}}}prop")
        SubElement(props, f"{{{D}}}getetag")
        SubElement(props, f"{{{C}}}calendar-data")
        filters = SubElement(root, f"{{{C}}}filter")
        comp = SubElement(filters, f"{{{C}}}comp-filter", {"name": "VCALENDAR"})
        SubElement(comp, f"{{{C}}}comp-filter", {"name": "VEVENT"})
        response = await self.request(
            "REPORT", path, tostring(root), {"Depth": "1", "Content-Type": "application/xml"}
        )
        result = {}
        for item in fromstring(response.content).findall(f"{{{D}}}response"):
            successful = [
                p for p in item.findall(f"{{{D}}}propstat") if " 200 " in p.findtext(f"{{{D}}}status", "")
            ]
            for block in successful:
                props = block.find(f"{{{D}}}prop")
                data = props.findtext(f"{{{C}}}calendar-data", "")
                if not data:
                    continue
                for event in Calendar.from_ical(data).walk("VEVENT"):
                    event_uid = str(event.get("UID", ""))
                    managed = (
                        str(event.get("X-EXAPP-EVENTS", "")) == "1"
                        if target == "internal"
                        else event_uid.startswith("exapp-events-public-")
                    )
                    if not managed:
                        continue
                    if event_uid in result:
                        raise ValueError("Обнаружены дубликаты UID в календаре")
                    start = event.decoded("DTSTART")
                    if not isinstance(start, datetime) or start.tzinfo is None:
                        raise ValueError(
                            "Сгенерированное событие имеет некорректную дату; требуется проверка календаря"
                        )
                    result[event_uid] = {
                        "uid": event_uid,
                        "href": self.safe_path(item.findtext(f"{{{D}}}href")),
                        "etag": props.findtext(f"{{{D}}}getetag", ""),
                        "hash": canonical_ical(data),
                        "file_id": str(event.get("X-EXAPP-EVENTS-SOURCE-FILE-ID", "")),
                        "source_key": str(event.get("X-EXAPP-EVENTS-SOURCE-KEY", "")),
                        "start": start.isoformat(),
                    }
        return result

    async def apply(self, operation: dict):
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if operation["etag"]:
            headers["If-Match"] = operation["etag"]
        else:
            headers["If-None-Match"] = "*"
        if operation["action"] == "delete":
            try:
                await self.request("DELETE", self.safe_path(operation["href"]), headers=headers)
            except DavError as exc:
                if exc.status != 404:
                    raise
        else:
            await self.request("PUT", self.safe_path(operation["href"]), operation["ical"].encode(), headers)
