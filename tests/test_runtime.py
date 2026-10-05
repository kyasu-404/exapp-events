from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ex_app.lib.runtime import Runtime
from ex_app.lib.services.mail_delivery import BRIDGE, MailDelivery


async def test_restore_waits_past_five_network_failures(tmp_path, monkeypatch):
    client = SimpleNamespace(ocs=AsyncMock(side_effect=[OSError("offline")] * 7 + [True]))
    runtime = Runtime(tmp_path, lambda: client)
    runtime.enable = AsyncMock()
    sleep = AsyncMock()
    monkeypatch.setattr("ex_app.lib.runtime.asyncio.sleep", sleep)
    await runtime.restore()
    assert client.ocs.await_count == 8
    assert max(call.args[0] for call in sleep.call_args_list) == 60
    runtime.enable.assert_awaited_once_with(True, client)
    runtime.store.close()


async def test_disabled_server_does_not_restore_workers(tmp_path):
    client = SimpleNamespace(ocs=AsyncMock(return_value=False))
    runtime = Runtime(tmp_path, lambda: client)
    runtime.enable = AsyncMock()
    await runtime.restore()
    runtime.enable.assert_not_called()
    runtime.store.close()


async def test_nextcloud_delivery_never_reads_or_copies_smtp_password(harness):
    _, _, _, settings, *_ = harness
    settings.smtp_mode = "nextcloud"
    client = SimpleNamespace(ocs=AsyncMock(return_value={"status": "sent"}))
    delivery = MailDelivery(lambda: client)
    await delivery.send({"kind": "test"}, "external@example.org", settings, "never-used-secret", "key")
    call = client.ocs.call_args
    assert call.args == ("POST", BRIDGE + "/send")
    assert call.kwargs["json"]["to"] == "external@example.org"
    assert "never-used-secret" not in str(call)
    assert "From" not in call.kwargs["json"]
    client.ocs.return_value = {"status": "ok"}
    await delivery.probe(settings)
    assert client.ocs.call_args.args == ("POST", BRIDGE + "/probe")


async def test_nextcloud_rejection_does_not_mark_queue_sent(harness):
    store, reminders, engine, settings, files, calendars, run = harness
    settings.smtp_mode = "nextcloud"
    client = SimpleNamespace(ocs=AsyncMock(return_value={"error": "mail_send_failed"}))
    reminders.delivery = MailDelivery(lambda: client)
    await run()
    from ex_app.lib.db.store import now

    store.execute("UPDATE reminders SET scheduled_at=?", (now(),))
    await reminders.tick(settings, "")
    assert all(j["status"] == "retry" for j in store.rows("SELECT status FROM reminders"))


async def test_probe_requires_acknowledged_connection(harness):
    _, _, _, settings, *_ = harness
    settings.smtp_mode = "nextcloud"
    delivery = MailDelivery(lambda: SimpleNamespace(ocs=AsyncMock(return_value={"error": "offline"})))
    with pytest.raises(ValueError):
        await delivery.probe(settings)
