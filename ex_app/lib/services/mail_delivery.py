import asyncio

from email_validator import validate_email

from . import smtp_service

BRIDGE = "/ocs/v2.php/apps/exapp_events_bridge/api/v1"


class MailDelivery:
    def __init__(self, factory):
        self.factory = factory

    async def send(self, payload, recipient, settings, password, dedupe_key):
        if settings.smtp_mode == "nextcloud":
            recipient = validate_email(recipient, check_deliverability=False).normalized
            subject, text, html = smtp_service.content(payload)
            result = await self.factory().ocs(
                "POST",
                BRIDGE + "/send",
                json={
                    "to": recipient,
                    "subject": subject,
                    "text": text,
                    "html": html,
                },
            )
            if result.get("status") != "sent":
                raise ValueError("Nextcloud SMTP bridge: отправка не подтверждена")
        else:
            await asyncio.to_thread(smtp_service.send, payload, recipient, settings, password, dedupe_key)

    async def probe(self, settings, password=""):
        if settings.smtp_mode == "nextcloud":
            result = await self.factory().ocs("POST", BRIDGE + "/probe")
            if result.get("status") != "ok":
                raise ValueError("Nextcloud SMTP bridge недоступен")
        else:
            await asyncio.to_thread(smtp_service.probe, settings, password)
