from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pytest

from screening.domain.interviews import (
    ZONE_LABELS,
    ZONE_LABELS_EN,
    IcsEvent,
    SlotBooking,
    build_ics,
    confirmation,
    in_quiet_hours,
    operation_key,
    recruiter_change_text,
    reminder_text,
    reminder_time,
)

MADRID, MEXICO = ZoneInfo("Europe/Madrid"), ZoneInfo("America/Mexico_City")


def at(
    year: int, month: int, day: int, hour: int, minute: int = 0, zone: ZoneInfo = MADRID
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=zone)


def slot(starts_at: datetime, timezone: str = "Europe/Madrid", address: str = "") -> SlotBooking:
    return SlotBooking("slot-1", "VLC-RUZ", "Sazón Ruzafa", address, timezone, starts_at)


@pytest.mark.parametrize(
    ("starts_at", "candidate_tz", "now", "expected"),
    [
        # Plain 24 h before, in the candidate's daytime.
        (at(2026, 11, 5, 10), "Europe/Madrid", at(2026, 11, 1, 9), at(2026, 11, 4, 10)),
        # 24 h before falls at 08:00 (quiet): move back to 20:30 the previous evening.
        (at(2026, 11, 5, 8), "Europe/Madrid", at(2026, 11, 1, 9), at(2026, 11, 3, 20, 30)),
        # 24 h before falls at 22:00 (quiet): 20:30 the same evening.
        (at(2026, 11, 5, 22), "Europe/Madrid", at(2026, 11, 1, 9), at(2026, 11, 4, 20, 30)),
        # Sooner than 24 h: 2 h before.
        (at(2026, 10, 28, 17), "Europe/Madrid", at(2026, 10, 28, 10), at(2026, 10, 28, 15)),
        # Mexico candidate and site: daytime there, not in Madrid.
        (
            at(2026, 11, 5, 10, zone=MEXICO),
            "America/Mexico_City",
            at(2026, 11, 1, 9, zone=MEXICO),
            at(2026, 11, 4, 10, zone=MEXICO),
        ),
        # Mexico quiet hours apply in Mexico time (08:30 there is quiet).
        (
            at(2026, 11, 5, 8, 30, zone=MEXICO),
            "America/Mexico_City",
            at(2026, 11, 1, 9, zone=MEXICO),
            at(2026, 11, 3, 20, 30, zone=MEXICO),
        ),
        # Candidate in Madrid, site in Mexico: 16:00 CDMX is 23:00 Madrid (quiet for them).
        (
            at(2026, 11, 5, 16, zone=MEXICO),
            "Europe/Madrid",
            at(2026, 11, 1, 9),
            at(2026, 11, 4, 20, 30),
        ),
        # Across the end of daylight saving time in Spain (25/10/2026).
        (at(2026, 10, 26, 10), "Europe/Madrid", at(2026, 10, 20, 9), at(2026, 10, 25, 10)),
    ],
)
def test_reminder_time(
    starts_at: datetime, candidate_tz: str, now: datetime, expected: datetime
) -> None:
    result = reminder_time(starts_at, candidate_tz, now)
    assert result == expected
    assert result is not None and result.tzinfo == UTC
    assert not in_quiet_hours(result, candidate_tz)


def test_no_reminder_when_both_targets_have_passed_or_are_quiet() -> None:
    # 2 h before is 08:00 (quiet) and 20:30 the evening before is already past.
    assert reminder_time(at(2026, 11, 5, 10), "Europe/Madrid", at(2026, 11, 4, 22)) is None
    # Interview in five minutes.
    assert reminder_time(at(2026, 11, 5, 10), "Europe/Madrid", at(2026, 11, 5, 9, 55)) is None


def test_confirmation_uses_candidate_timezone_and_site_time_when_different() -> None:
    booked = slot(at(2026, 11, 5, 10, zone=MEXICO), "America/Mexico_City", "Av. Juárez 10")
    data = confirmation(booked, "Europe/Madrid")
    assert data["when"] == "jueves 05/11/2026 a las 17:00"
    assert data["timezone_label"] == "hora peninsular de España"
    assert data["site_local_time"].startswith("jueves 05/11/2026 a las 10:00")
    assert data["address"] == "Av. Juárez 10"
    assert data["duration_minutes"] == 15
    assert "cancelar" in data["how_to_change"]
    same = confirmation(slot(at(2026, 11, 5, 10)), "Europe/Madrid", rescheduled=True)
    assert "site_local_time" not in same and same["address"] is None
    assert same["must_confirm"].startswith("Confirma el cambio")
    assert "15 minutos" in reminder_text(booked, "America/Mexico_City", "Grupo Sazón")


RUZAFA = slot(at(2026, 11, 5, 11), address="C/ Sueca 20")


@pytest.mark.parametrize(
    ("notice", "timezone", "language", "expected"),
    [
        (
            "reminder",
            "Europe/Madrid",
            "en",
            "Reminder from Grupo Sazón: you have a 15-minute interview on Thursday "
            "5 November 2026 at 11:00 (mainland Spain time) at Sazón Ruzafa (C/ Sueca 20).",
        ),
        (
            "reminder",
            "Europe/Madrid",
            "es",
            "Recordatorio de Grupo Sazón: tienes una entrevista de 15 minutos el jueves "
            "05/11/2026 a las 11:00 (hora peninsular de España) en Sazón Ruzafa (C/ Sueca 20).",
        ),
        (
            "reschedule",
            "Europe/Madrid",
            "en",
            "The Grupo Sazón hiring team has moved your interview to Thursday 5 November 2026 "
            "at 11:00 (mainland Spain time) at Sazón Ruzafa.",
        ),
        (
            "reschedule",
            "Europe/Madrid",
            "es",
            "El equipo de selección de Grupo Sazón ha cambiado tu entrevista al jueves "
            "05/11/2026 a las 11:00 (hora peninsular de España) en Sazón Ruzafa.",
        ),
        (
            "cancel",
            "Europe/Madrid",
            "es",
            "El equipo de selección de Grupo Sazón ha cancelado tu entrevista del jueves "
            "05/11/2026 a las 11:00 (hora peninsular de España). Si quieres otra fecha, "
            "escríbenos aquí.",
        ),
        (
            "cancel",
            "Europe/Madrid",
            "fr",
            "El equipo de selección de Grupo Sazón ha cancelado tu entrevista del jueves",
        ),
        (
            "cancel",
            "America/Mexico_City",
            "en",
            "The Grupo Sazón hiring team has cancelled your interview on Thursday 5 November "
            "2026 at 04:00 (central Mexico time).",
        ),
    ],
)
def test_candidate_notice_text_per_language(
    notice: Literal["reminder", "reschedule", "cancel"], timezone: str, language: str, expected: str
) -> None:
    text = (
        reminder_text(RUZAFA, timezone, "Grupo Sazón", language)
        if notice == "reminder"
        else recruiter_change_text(notice, RUZAFA, timezone, "Grupo Sazón", language)
    )
    assert text.startswith(expected)


def test_unsupported_language_falls_back_to_spanish() -> None:
    assert reminder_text(RUZAFA, "Europe/Madrid", "Grupo Sazón", "fr") == reminder_text(
        RUZAFA, "Europe/Madrid", "Grupo Sazón", "es"
    )


def test_every_zone_label_has_an_english_label() -> None:
    assert ZONE_LABELS.keys() == ZONE_LABELS_EN.keys()


def test_operation_keys_are_stable_and_scoped() -> None:
    assert operation_key("s", "b", "cancel") == operation_key("s", "b", "cancel")
    assert operation_key("s", "b", "reschedule", 0, "x") != operation_key(
        "s", "b", "reschedule", 1, "x"
    )
    assert operation_key("s", "b", "cancel") != operation_key("other", "b", "cancel")


def event(
    method: Literal["PUBLISH", "CANCEL"] = "PUBLISH", sequence: int = 0, zone: ZoneInfo = MADRID
) -> IcsEvent:
    timezone = "Europe/Madrid" if zone is MADRID else "America/Mexico_City"
    return IcsEvent(
        uid="abc@agenda.orbio",
        sequence=sequence,
        method=method,
        slot=SlotBooking(
            "slot-1",
            "VLC-RUZ",
            "Sazón Ruzafa",
            "Calle de Sueca, 20; bajo",
            timezone,
            at(2026, 11, 5, 10, zone=zone).astimezone(UTC),
        ),
        summary="Entrevista con Grupo Sazón · Sazón Ruzafa",
        description="Entrevista de 15 minutos. " + "Texto largo con acentos áéíóú. " * 6,
        organizer_name='Grupo "Sazón"',
        organizer_email="no-reply@orbio.invalid",
        stamp=datetime(2026, 10, 28, 12, tzinfo=UTC),
    )


def unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


def test_ics_publish_is_rfc5545_with_vtimezone() -> None:
    text = build_ics(event())
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    text.encode().decode("utf-8")  # folding never splits a multibyte character
    lines = unfold(text)
    for expected in (
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "METHOD:PUBLISH",
        "UID:abc@agenda.orbio",
        "SEQUENCE:0",
        "DTSTAMP:20261028T120000Z",
        "DTSTART;TZID=Europe/Madrid:20261105T100000",
        "DTEND;TZID=Europe/Madrid:20261105T101500",
        "LOCATION:Sazón Ruzafa\\, Calle de Sueca\\, 20\\; bajo",
        "STATUS:CONFIRMED",
        "TZID:Europe/Madrid",
        "BEGIN:VALARM",
        "ORGANIZER;CN=\"Grupo 'Sazón'\":mailto:no-reply@orbio.invalid",
    ):
        assert expected in lines, expected
    timezone = lines[lines.index("BEGIN:VTIMEZONE") : lines.index("END:VTIMEZONE")]
    # Daylight saving in 2026: 29/03 02:00 (+0100 -> +0200) and 25/10 03:00 (+0200 -> +0100).
    assert "DTSTART:20260329T020000" in timezone
    assert "DTSTART:20261025T030000" in timezone
    daylight = timezone.index("BEGIN:DAYLIGHT")
    assert timezone[daylight + 2 : daylight + 4] == ["TZOFFSETFROM:+0100", "TZOFFSETTO:+0200"]


def test_ics_mexico_has_single_standard_observance() -> None:
    lines = unfold(build_ics(event(zone=MEXICO)))
    timezone = lines[lines.index("BEGIN:VTIMEZONE") : lines.index("END:VTIMEZONE")]
    assert "TZID:America/Mexico_City" in timezone
    assert "BEGIN:DAYLIGHT" not in timezone
    assert "TZOFFSETTO:-0600" in timezone
    assert "DTSTART;TZID=America/Mexico_City:20261105T100000" in lines


def test_ics_cancel_keeps_uid_and_bumps_sequence() -> None:
    lines = unfold(build_ics(event("CANCEL", 2)))
    assert "METHOD:CANCEL" in lines and "STATUS:CANCELLED" in lines
    assert "UID:abc@agenda.orbio" in lines and "SEQUENCE:2" in lines
    assert "BEGIN:VALARM" not in lines


def test_reminder_rule_constants_match_documented_window() -> None:
    assert in_quiet_hours(at(2026, 11, 5, 21), "Europe/Madrid")
    assert in_quiet_hours(at(2026, 11, 5, 8, 59), "Europe/Madrid")
    assert not in_quiet_hours(at(2026, 11, 5, 9), "Europe/Madrid")
    assert not in_quiet_hours(at(2026, 11, 5, 20, 59), "Europe/Madrid")
    assert at(2026, 11, 5, 9) - timedelta(hours=24) == at(2026, 11, 4, 9)
