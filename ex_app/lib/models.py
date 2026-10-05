import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db.store import dumps

HEADERS = {
    "location": "Аудитория",
    "time": "Время",
    "title": "Мероприятие",
    "responsible": "Ответственный",
    "emails": "Email для уведомления",
    "offsets": "Напомнить за",
    "public": "На сайт",
}


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_owner: str = ""
    source_id: str = ""
    source_path: str = ""
    recursive: bool = True
    include: str = "*.xlsx"
    exclude: str = "~$*"
    debounce_seconds: int = Field(10, ge=1, le=300)
    reconciliation_minutes: Literal[0, 5, 15, 30, 60, 360, 1440] = 30
    reconcile_on_start: bool = True
    check_month: bool = True
    headers: dict[str, str] = Field(default_factory=lambda: dict(HEADERS))
    duration_minutes: int = Field(60, ge=5, le=1440)
    timezone: str = "Europe/Moscow"
    missing_file_policy: Literal["future", "all", "keep"] = "future"
    calendar_owner: str = ""
    internal_calendar: str = ""
    public_enabled: bool = False
    public_calendar: str = ""
    public_link: str = ""
    smtp_enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_user: str = ""
    smtp_sender: str = ""
    smtp_name: str = "Мероприятия"
    overdue_minutes: int = Field(0, ge=0, le=1440)
    notify_changes: bool = True
    notify_cancellation: bool = True
    retention_days: Literal[30, 90, 180, 365] = 90
    delete_guard: bool = True
    delete_percent: int = Field(25, ge=1, le=100)
    delete_minimum: int = Field(20, ge=1, le=10000)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Выберите существующий часовой пояс IANA") from None
        return value

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value):
        if set(value) != set(HEADERS) or not all(v.strip() for v in value.values()):
            raise ValueError("Задайте все семь заголовков Excel")
        normalized = [" ".join(v.split()).casefold() for v in value.values()]
        if len(set(normalized)) != len(value):
            raise ValueError("Заголовки должны различаться")
        return value

    @field_validator("smtp_host", "smtp_user", "smtp_sender", "smtp_name", "source_owner", "calendar_owner")
    @classmethod
    def no_control_chars(cls, value):
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("Недопустимые управляющие символы")
        return value.strip()

    @model_validator(mode="after")
    def calendars_distinct(self):
        if self.public_enabled and not self.public_calendar:
            raise ValueError("Выберите публичный календарь для публикации")
        if self.public_calendar and self.public_calendar == self.internal_calendar:
            raise ValueError("Внутренний и публичный календари должны различаться")
        return self


@dataclass
class Event:
    source_key: str
    file_id: str
    sheet: str
    start: str
    end: str
    location: str
    title: str
    responsible: str
    emails: list[str] = field(default_factory=list)
    offsets: list[int] = field(default_factory=list)
    public: bool = False
    revision: int = 1

    def data(self):
        return asdict(self)

    def hash(self):
        return digest({k: v for k, v in self.data().items() if k not in {"revision", "sheet"}})

    @property
    def starts_at(self):
        return datetime.fromisoformat(self.start)


def digest(value) -> str:
    return hashlib.sha256(dumps(value).encode()).hexdigest()
