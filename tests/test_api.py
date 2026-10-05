import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree.ElementTree import parse

import pytest
from fastapi.testclient import TestClient
from nc_py_api.ex_app import anc_app

from ex_app.lib.main import create_app
from ex_app.lib.models import Settings
from ex_app.lib.services.event_listener import CALLBACK, EVENTS, enqueue, register_listener


class FakeNC:
    def __init__(self, user="admin", shared=None):
        self.user_id = user
        self.shared = shared if shared is not None else {}
        self.app_cfg = SimpleNamespace(
            app_name="exapp_events",
            endpoint="https://nextcloud.example/",
            dav_endpoint="https://nextcloud.example/remote.php/dav/",
        )
        self.users = SimpleNamespace(
            get_user=AsyncMock(
                return_value=SimpleNamespace(enabled=True, groups=["admin"] if user == "admin" else ["users"])
            )
        )
        self.ui = SimpleNamespace(
            top_menu=SimpleNamespace(register=AsyncMock()),
            resources=SimpleNamespace(set_script=AsyncMock(), set_style=AsyncMock()),
        )
        self.webhooks = SimpleNamespace(get_list=AsyncMock(return_value=[]), register=AsyncMock())
        self.appconfig_ex = SimpleNamespace(
            get_value=self.get_value, set_value=self.set_value, delete=self.delete
        )
        self.set_init_status = AsyncMock()

    @property
    async def user(self):
        return self.user_id

    async def set_user(self, user):
        self.user_id = user

    async def get_value(self, key, default=""):
        return self.shared.get(key, default)

    async def set_value(self, key, value, sensitive=None):
        assert sensitive is True
        self.shared[key] = value

    async def delete(self, key):
        self.shared.pop(key, None)

    async def users_list(self):
        return ["admin", "user"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key, value in {
        "APP_ID": "exapp_events",
        "APP_SECRET": "test-secret",
        "APP_VERSION": "0.1.0",
        "NEXTCLOUD_URL": "http://localhost",
    }.items():
        monkeypatch.setenv(key, value)
    shared = {}

    def factory():
        return FakeNC(shared=shared)

    app = create_app(tmp_path, factory, restore=False)

    async def nc(request):
        return FakeNC(request.scope.get("username", ""), shared)

    # Keep real AppAPI middleware and backend admin check; replace only the network SDK client.
    from fastapi import Request

    nc.__annotations__["request"] = Request
    app.dependency_overrides[anc_app] = nc
    with TestClient(app) as test_client:
        yield test_client, app, shared


def headers(user="admin", secret="test-secret"):
    return {
        "EX-APP-ID": "exapp_events",
        "EX-APP-VERSION": "0.1.0",
        "AUTHORIZATION-APP-API": base64.b64encode(f"{user}:{secret}".encode()).decode(),
    }


def test_admin_and_normal_user(client):
    http, app, shared = client
    assert http.get("/api/status", headers=headers()).status_code == 200
    assert http.get("/api/status", headers=headers("user")).status_code == 403
    assert http.get("/api/settings", headers=headers("user")).status_code == 403
    assert http.get("/api/settings", headers=headers("")).status_code == 403
    assert http.get("/api/status").status_code == 401
    assert http.get("/heartbeat").json() == {"status": "ok"}


def test_wrong_secret_never_logged(client, capsys):
    http, app, shared = client
    for auth in [headers(secret="supplied-secret"), {"AUTHORIZATION-APP-API": "malformed"}, {}]:
        assert http.get("/api/status", headers=auth).status_code == 401
    output = capsys.readouterr()
    assert "test-secret" not in output.out + output.err and "supplied-secret" not in output.out + output.err


def test_sensitive_password_saved_never_returned(client):
    http, app, shared = client
    body = {"settings": Settings().model_dump(), "smtp_password": "sensitive-password"}
    assert http.put("/api/settings", headers=headers(), json=body).status_code == 200
    assert shared["smtp_password"] == "sensitive-password"
    response = http.get("/api/settings", headers=headers())
    assert response.json()["smtp_password_set"] and "sensitive-password" not in response.text
    body["settings"]["duration_minutes"] = 0
    invalid = http.put("/api/settings", headers=headers(), json=body)
    assert invalid.status_code == 422 and "sensitive-password" not in invalid.text
    assert "sensitive-password" not in str(app.state.runtime.store.rows("SELECT * FROM logs"))


def test_invalid_timezone_is_validation_error(client):
    http, app, shared = client
    settings = Settings().model_dump()
    settings["timezone"] = "invalid/timezone"
    response = http.put("/api/settings", headers=headers(), json={"settings": settings})
    assert response.status_code == 422


def test_public_calendar_cannot_alias_internal_url(client):
    http, app, shared = client
    settings = Settings(
        calendar_owner="admin",
        internal_calendar="calendars/admin/events/",
        public_enabled=True,
        public_calendar="https://nextcloud.example/remote.php/dav/calendars/admin/events/",
    )
    response = http.put("/api/settings", headers=headers(), json={"settings": settings.model_dump()})
    assert response.status_code == 422 and "должны различаться" in response.text


def test_lifecycle_and_callback_do_not_require_admin(client):
    http, app, shared = client
    assert http.post("/init", headers=headers("")).status_code == 200
    assert http.put("/enabled?enabled=true", headers=headers("")).json() == {"error": ""}
    assert app.state.runtime.enabled
    assert (
        http.post(
            CALLBACK,
            headers=headers("user"),
            json={
                "event": {
                    "class": "OCP\\Files\\Events\\Node\\NodeWrittenEvent",
                    "node": {"id": 42, "path": "/a.xlsx"},
                }
            },
        ).status_code
        == 200
    )
    assert http.put("/enabled?enabled=false", headers=headers("")).json() == {"error": ""}
    assert not app.state.runtime.enabled and app.state.runtime.store.path.exists()


def test_admin_routes_declared():
    routes = parse("appinfo/info.xml").findall(".//routes/route")
    assert next(r for r in routes if r.findtext("url") == "^/api/.*$").findtext("access_level") == "ADMIN"
    assert (
        next(r for r in routes if r.findtext("url") == "^/(js|css)/.*$").findtext("access_level") == "ADMIN"
    )
    assert not any("events/files" in r.findtext("url") for r in routes)


async def test_listener_registration_idempotent():
    nc = FakeNC()
    await register_listener(nc)
    assert nc.webhooks.register.await_count == 4
    for call, subtype in zip(nc.webhooks.register.call_args_list, EVENTS):
        assert call.args == ("POST", CALLBACK, "OCP\\Files\\Events\\Node\\" + subtype)
    nc.webhooks.get_list.return_value = [
        SimpleNamespace(event="OCP\\Files\\Events\\Node\\" + e, app_id="exapp_events") for e in EVENTS
    ]
    nc.webhooks.register.reset_mock()
    await register_listener(nc)
    nc.webhooks.register.assert_not_called()


def test_debounce_and_no_authentication_storage(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    settings.source_path = "/Мероприятия"
    payload = {
        "event": {
            "class": "OCP\\Files\\Events\\Node\\NodeWrittenEvent",
            "node": {"id": 42, "path": "/admin/files/Мероприятия/2027/file.xlsx"},
        },
        "authentication": {"password": "webhook-sensitive"},
    }
    for _ in range(5):
        assert enqueue(store, payload, settings)
    assert len(store.rows("SELECT * FROM event_queue")) == 1
    assert "webhook-sensitive" not in str(store.rows("SELECT * FROM event_queue"))
    payload["event"]["node"]["path"] = "/elsewhere/file.xlsx"
    assert not enqueue(store, payload, settings)
