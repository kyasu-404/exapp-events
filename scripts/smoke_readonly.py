"""Read-only Files / CalDAV smoke check inside the installed ExApp container."""

import argparse
import asyncio
import json

from nc_py_api import AsyncNextcloudApp

from ex_app.lib.services.calendar_service import CalendarService


async def check(owner):
    nc = AsyncNextcloudApp()
    await nc.set_user(owner)
    root = await nc.files.by_path("/")
    calendars = await CalendarService(nc, owner).list_calendars()
    print(
        json.dumps(
            {
                "files_root_readable": bool(root and root.is_dir),
                "calendars_readable": len(calendars),
                "calendars_writable": sum(c["writable"] for c in calendars),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    asyncio.run(check(args.owner))
