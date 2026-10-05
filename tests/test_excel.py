from datetime import date, datetime, time

import pytest

from ex_app.lib.models import HEADERS, Settings
from ex_app.lib.services.excel_parser import file_context, parse_emails, parse_offsets, parse_time, parse_xlsx

from .conftest import ROW, workbook


@pytest.mark.parametrize(
    "sheet,expected",
    [("21.09", "2026-09-21"), ("1.10", "2026-10-01"), (" 01.10. ", "2026-10-01"), ("1.1", "2026-01-01")],
)
def test_dates(sheet, expected):
    result = parse_xlsx(workbook([ROW], sheet), "42", "/2025/a/2026/b/Сетка.xlsx", Settings())
    assert result.events[0].start[:10] == expected


def test_unknown_year_and_month_mismatch():
    assert not parse_xlsx(workbook([ROW]), "42", "/Сетка.xlsx", Settings()).safe_delete
    result = parse_xlsx(workbook([ROW], "15.10"), "42", "/2026/Сентябрь/file.xlsx", Settings())
    assert not result.events and not result.safe_delete and result.issues[0]["level"] == "Warning"
    assert file_context("/2026/Extra/сЕНТЯБРЬ/x.xlsx") == (2026, 9)


@pytest.mark.parametrize(
    "value,ending",
    [
        (time(15, 30), "16:30"),
        (datetime(2000, 1, 1, 15, 30), "16:30"),
        ("15:30", "16:30"),
        ("15.30", "16:30"),
        ("15:30-17:00", "17:00"),
        ("15:30 – 17:00", "17:00"),
        ("15.30–17.00", "17:00"),
    ],
)
def test_times(value, ending):
    start, end = parse_time(value, date(2026, 9, 21), Settings())
    assert start.hour == 15 and start.minute == 30
    assert end.strftime("%H:%M") == ending and start.utcoffset().total_seconds() == 10800


@pytest.mark.parametrize("value", ["25:00", "15:90", "wrong", "17:00-15:00", "15:30-17:00-18:00"])
def test_bad_time(value):
    with pytest.raises(ValueError):
        parse_time(value, date(2026, 9, 21), Settings())


def test_columns_reorder_blank_hidden_and_custom_headers():
    headers = list(reversed(list(HEADERS.values())))
    result = parse_xlsx(
        workbook([list(reversed(ROW)), [None] * 7], headers=headers),
        "42",
        "/2026/Сентябрь/file.xlsx",
        Settings(),
    )
    assert len(result.events) == 1 and result.events[0].title == ROW[2]
    settings = Settings()
    settings.headers["title"] = "Название"
    custom = list(HEADERS.values())
    custom[2] = "Название"
    assert parse_xlsx(workbook([ROW], headers=custom), "42", "/2026/file.xlsx", settings).events


def test_bad_header_formula_and_corrupt():
    assert not parse_xlsx(
        workbook([ROW], headers=["Нет"] * 7), "42", "/2026/file.xlsx", Settings()
    ).safe_delete
    row = list(ROW)
    row[2] = '=CONCAT("A","B")'
    result = parse_xlsx(workbook([row]), "42", "/2026/file.xlsx", Settings())
    assert not result.events and not result.safe_delete
    assert not parse_xlsx(b"broken", "42", "/2026/file.xlsx", Settings()).safe_delete


def test_conflict_removed_both_rows():
    result = parse_xlsx(workbook([ROW, ROW]), "42", "/2026/file.xlsx", Settings())
    assert not result.events and not result.safe_delete


def test_optional_errors_do_not_block_calendar():
    row = list(ROW)
    row[4] = "Good@example.org; good@example.org, broken\nsecond@example.com"
    row[5] = "24ч;2h;bad"
    result = parse_xlsx(workbook([row]), "42", "/2026/file.xlsx", Settings())
    assert result.safe_delete and len(result.events) == 1
    assert len(result.events[0].emails) == 2 and result.events[0].offsets == [7200, 86400]
    assert len(result.issues) == 2


@pytest.mark.parametrize(
    "raw,seconds",
    [
        ("30м", 1800),
        ("1ч", 3600),
        ("2ч", 7200),
        ("6h", 21600),
        ("12ч", 43200),
        ("24ч", 86400),
        ("48ч", 172800),
        ("1д", 86400),
        ("2d", 172800),
    ],
)
def test_offsets(raw, seconds):
    assert parse_offsets(raw) == ([seconds], [])


@pytest.mark.parametrize(
    "flag,enabled",
    [
        ("Да", True),
        ("yes", True),
        ("true", True),
        (1, True),
        ("+", True),
        ("Нет", False),
        (0, False),
        ("-", False),
        (None, False),
    ],
)
def test_public_flags(flag, enabled):
    row = list(ROW)
    row[6] = flag
    assert parse_xlsx(workbook([row]), "42", "/2026/file.xlsx", Settings()).events[0].public == enabled


def test_dst_nonexistent_and_ambiguous():
    for day in [date(2026, 3, 29), date(2026, 10, 25)]:
        with pytest.raises(ValueError):
            parse_time("02:30", day, Settings(timezone="Europe/Berlin"))


def test_email_empty_and_delimiters():
    assert parse_emails("") == ([], [])
    assert len(parse_emails("a@example.org;b@example.org,c@example.org\nd@example.org")[0]) == 4


def test_missing_optional_headers_warn_without_blocking_event():
    result = parse_xlsx(
        workbook([ROW[:4]], headers=list(HEADERS.values())[:4]), "42", "/2027/file.xlsx", Settings()
    )
    assert len(result.events) == 1 and result.safe_delete
    assert len(result.issues) == 3 and all(i["level"] == "Warning" for i in result.issues)


def test_headers_after_fifty_empty_rows_are_rejected():
    from io import BytesIO

    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "21.09"
    for _ in range(50):
        book.active.append([None])
    book.active.append(list(HEADERS.values()))
    book.active.append(ROW)
    stream = BytesIO()
    book.save(stream)
    book.close()
    result = parse_xlsx(stream.getvalue(), "42", "/2027/file.xlsx", Settings())
    assert not result.safe_delete and not result.events
