import base64
import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException
from nc_py_api import AsyncNextcloudApp
from nc_py_api.ex_app import anc_app
from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class SecretGuard:
    """Reject malformed authentication silently before SDK middleware.

    nc_py_api 0.30.3 sign_check formats supplied/expected secrets in its ValueError.
    The SDK middleware prints that error. This gate prevents that path entirely.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/heartbeat":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        valid = False
        try:
            value = base64.b64decode(headers.get("AUTHORIZATION-APP-API", ""), validate=True).decode()
            user, secret = value.split(":", 1)
            valid = (
                bool(os.environ.get("APP_SECRET"))
                and bool(headers.get("EX-APP-VERSION"))
                and headers.get("EX-APP-ID") == os.environ.get("APP_ID")
                and hmac.compare_digest(secret.encode(), os.environ["APP_SECRET"].encode())
            )
        except (ValueError, UnicodeError):
            pass
        if not valid:
            await JSONResponse({"message": "AppAPI authentication required"}, status_code=401)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)


async def require_admin(nc: Annotated[AsyncNextcloudApp, Depends(anc_app)]):
    if not await nc.user:
        raise HTTPException(403, "Требуется администратор Nextcloud")
    try:
        info = await nc.users.get_user(await nc.user)
        is_admin = info.enabled and "admin" in info.groups
    except Exception:
        is_admin = False
    if not is_admin:
        raise HTTPException(403, "Требуется администратор Nextcloud")
    return nc
