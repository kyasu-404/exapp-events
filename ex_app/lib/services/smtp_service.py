import html
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

from email_validator import validate_email

from ..models import Settings


def content(payload: dict) -> tuple[str, str, str]:
    kind = payload.get("kind", "reminder")
    label = {
        "reminder": "Напоминание",
        "change": "Изменено мероприятие",
        "cancel": "Отменено мероприятие",
        "test": "Тестовое письмо",
    }[kind]
    event = payload.get("event")
    if event:
        start = datetime.fromisoformat(event["start"])
        subject = f"{label}: {event['title']} — {start:%d.%m.%Y %H:%M}"
        body = f"{label}:\n\n{event['title']}\n\nДата: {start:%d.%m.%Y}\nВремя: {start:%H:%M}\nМесто: {event['location']}\nОтветственный: {event['responsible']}\n\nЭто автоматическое уведомление."
    else:
        subject, body = "Мероприятия: тест SMTP", "SMTP настроен. Это автоматическое тестовое письмо."
    return (
        subject.replace("\r", " ").replace("\n", " "),
        body,
        "<html><body><p>" + html.escape(body).replace("\n", "<br>") + "</p></body></html>",
    )


def message(payload: dict, recipient: str, settings: Settings, dedupe_key: str) -> EmailMessage:
    recipient = validate_email(recipient, check_deliverability=False).normalized
    sender = validate_email(settings.smtp_sender, check_deliverability=False).normalized
    subject, body, html_body = content(payload)
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = formataddr((settings.smtp_name, sender))
    mail["To"] = recipient
    mail["Message-ID"] = f"<{dedupe_key}@{sender.split('@')[1]}>"
    mail.set_content(body)
    mail.add_alternative(html_body, subtype="html")
    return mail


def connect(settings: Settings, password: str):
    context = ssl.create_default_context()
    if settings.smtp_security == "tls":
        client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context)
    else:
        client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    try:
        client.ehlo()
        if settings.smtp_security == "starttls":
            client.starttls(context=context)
            client.ehlo()
        if settings.smtp_user:
            client.login(settings.smtp_user, password)
        return client
    except Exception:
        client.close()
        raise


def send(payload, recipient, settings, password, dedupe_key):
    mail = message(payload, recipient, settings, dedupe_key)
    with connect(settings, password) as client:
        client.send_message(mail)


def probe(settings, password):
    with connect(settings, password) as client:
        client.noop()
