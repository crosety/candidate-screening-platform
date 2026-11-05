"""Pure interview-agenda rules: confirmation data, reminder timing and iCalendar output."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

INTERVIEW_MINUTES = 15
ACTIVE_STATUSES = ("booked", "rescheduled")
QUIET_START = time(21)
QUIET_END = time(9)
EVENING_FALLBACK = time(20, 30)
REMINDER_LEAD = timedelta(hours=24)
SHORT_LEAD = timedelta(hours=2)
MIN_NOTICE = timedelta(minutes=10)
WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
ZONE_LABELS = {
    "Europe/Madrid": "hora peninsular de España",
    "Atlantic/Canary": "hora de Canarias",
    "America/Mexico_City": "hora del centro de México",
    "America/Monterrey": "hora de Monterrey",
    "America/Cancun": "hora de Cancún",
    "America/Tijuana": "hora de Tijuana",
}
HOW_TO_CHANGE = (
    "Para cambiarla o cancelarla, vuelve a esta conversación con tu enlace personal "
    "y dínoslo; también puedes pedirlo al equipo de selección."
)
# Deterministic notices the platform posts in the candidate's conversation language.
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
ZONE_LABELS_EN = {
    "Europe/Madrid": "mainland Spain time",
    "Atlantic/Canary": "Canary Islands time",
    "America/Mexico_City": "central Mexico time",
    "America/Monterrey": "Monterrey time",
    "America/Cancun": "Cancún time",
    "America/Tijuana": "Tijuana time",
}
HOW_TO_CHANGE_EN = (
    "To change or cancel it, come back to this conversation with your personal link "
    "and tell us; you can also ask the hiring team."
)


@dataclass(frozen=True)
class SlotBooking:
    """What the calendar confirmed for one interview slot."""

    slot_ref: str
    site_ref: str
    site_name: str
    site_address: str
    site_timezone: str
    starts_at: datetime
    duration_minutes: int = INTERVIEW_MINUTES

    @classmethod
    def from_calendar(cls, data: dict[str, Any]) -> "SlotBooking":
        starts_at = datetime.fromisoformat(str(data["starts_at"]))
        if starts_at.tzinfo is None:
            raise ValueError("The calendar must return an aware start time")
        return cls(
            str(data["slot_id"]),
            str(data.get("site_id", "")),
            str(data["site_name"]),
            str(data.get("site_address") or ""),
            str(data.get("site_timezone") or "UTC"),
            starts_at.astimezone(UTC),
            int(data.get("duration_minutes") or INTERVIEW_MINUTES),
        )


def operation_key(session_id: object, booking_id: object, action: str, *parts: object) -> str:
    """Stable idempotency key for one change of one booking within one session."""
    raw = ":".join(str(p) for p in (session_id, booking_id, action, *parts))
    return hashlib.sha256(raw.encode()).hexdigest()


def zone_label(timezone: str, language: str = "es") -> str:
    labels = ZONE_LABELS_EN if language == "en" else ZONE_LABELS
    return labels.get(timezone, timezone)


def local_text(at: datetime, timezone: str, language: str = "es") -> str:
    local = at.astimezone(ZoneInfo(timezone))
    if language == "en":
        month = MONTHS_EN[local.month - 1]
        return f"{WEEKDAYS_EN[local.weekday()]} {local.day} {month} {local.year} at {local:%H:%M}"
    return f"{WEEKDAYS[local.weekday()]} {local:%d/%m/%Y} a las {local:%H:%M}"


def in_quiet_hours(at: datetime, timezone: str) -> bool:
    local = at.astimezone(ZoneInfo(timezone)).time()
    return local >= QUIET_START or local < QUIET_END


def _before_quiet(at: datetime, timezone: str) -> datetime:
    """Latest permitted evening moment before the quiet window that contains ``at``."""
    zone = ZoneInfo(timezone)
    local = at.astimezone(zone)
    day = local.date() if local.time() >= QUIET_START else local.date() - timedelta(days=1)
    return datetime.combine(day, EVENING_FALLBACK, tzinfo=zone).astimezone(UTC)


def reminder_time(starts_at: datetime, candidate_timezone: str, now: datetime) -> datetime | None:
    """When to remind: ~24 h before, else ~2 h before, never in 21:00-09:00 local time.

    A target inside the quiet window moves back to 20:30 of the previous evening, so the
    reminder is never later than intended. If neither target is still ahead of ``now``,
    there is no reminder.
    """
    for lead in (REMINDER_LEAD, SHORT_LEAD):
        at = starts_at - lead
        if in_quiet_hours(at, candidate_timezone):
            at = _before_quiet(at, candidate_timezone)
        if at >= now + MIN_NOTICE:
            return at.astimezone(UTC)
    return None


def confirmation(
    slot: SlotBooking, candidate_timezone: str, *, rescheduled: bool = False
) -> dict[str, Any]:
    """Facts the agent must confirm, in the candidate's timezone."""
    data: dict[str, Any] = {
        "when": local_text(slot.starts_at, candidate_timezone),
        "timezone": candidate_timezone,
        "timezone_label": zone_label(candidate_timezone),
        "site_name": slot.site_name,
        "address": slot.site_address or None,
        "duration_minutes": slot.duration_minutes,
        "with": "la persona encargada del local",
        "how_to_change": HOW_TO_CHANGE,
        "calendar_file": "Puede añadirla a su calendario desde su conversación.",
    }
    if slot.site_timezone != candidate_timezone:
        data["site_local_time"] = (
            f"{local_text(slot.starts_at, slot.site_timezone)} ({zone_label(slot.site_timezone)})"
        )
    data["must_confirm"] = (
        "Confirma "
        + ("el cambio: " if rescheduled else "")
        + "fecha y hora con su zona horaria, local"
        + (" y dirección" if slot.site_address else "")
        + ", que dura 15 minutos y cómo cambiarla o cancelarla. No añadas otros datos."
    )
    return data


def _when(slot: SlotBooking, candidate_timezone: str, language: str) -> str:
    return (
        f"{local_text(slot.starts_at, candidate_timezone, language)} "
        f"({zone_label(candidate_timezone, language)})"
    )


def reminder_text(
    slot: SlotBooking, candidate_timezone: str, tenant_name: str, language: str = "es"
) -> str:
    where = slot.site_name + (f" ({slot.site_address})" if slot.site_address else "")
    when = _when(slot, candidate_timezone, language)
    if language == "en":
        return (
            f"Reminder from {tenant_name}: you have a {slot.duration_minutes}-minute interview "
            f"on {when} at {where}. {HOW_TO_CHANGE_EN}"
        )
    return (
        f"Recordatorio de {tenant_name}: tienes una entrevista de {slot.duration_minutes} "
        f"minutos el {when} en {where}. {HOW_TO_CHANGE}"
    )


def recruiter_change_text(
    action: Literal["cancel", "reschedule"],
    slot: SlotBooking,
    candidate_timezone: str,
    tenant_name: str,
    language: str = "es",
) -> str:
    """Tells the candidate that the hiring team cancelled or moved their interview."""
    when = _when(slot, candidate_timezone, language)
    if language == "en":
        if action == "cancel":
            return (
                f"The {tenant_name} hiring team has cancelled your interview on {when}. "
                "If you would like another date, write to us here."
            )
        return (
            f"The {tenant_name} hiring team has moved your interview to {when} "
            f"at {slot.site_name}. {HOW_TO_CHANGE_EN}"
        )
    if action == "cancel":
        return (
            f"El equipo de selección de {tenant_name} ha cancelado tu entrevista del {when}. "
            "Si quieres otra fecha, escríbenos aquí."
        )
    return (
        f"El equipo de selección de {tenant_name} ha cambiado tu entrevista al {when} "
        f"en {slot.site_name}. {HOW_TO_CHANGE}"
    )


# --- iCalendar (RFC 5545) -------------------------------------------------------------------


@dataclass(frozen=True)
class IcsEvent:
    uid: str
    sequence: int
    method: Literal["PUBLISH", "CANCEL"]
    slot: SlotBooking
    summary: str
    description: str
    organizer_name: str
    organizer_email: str
    stamp: datetime


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Fold at 75 octets without splitting a UTF-8 character (RFC 5545 3.1)."""
    parts: list[str] = []
    current, size, limit = "", 0, 75
    for char in line:
        width = len(char.encode())
        if size + width > limit:
            parts.append(current)
            current, size, limit = " ", 1, 75
        current += char
        size += width
    parts.append(current)
    return parts


def _utc(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _local(at: datetime, timezone: str) -> str:
    return at.astimezone(ZoneInfo(timezone)).strftime("%Y%m%dT%H%M%S")


def _offset(delta: timedelta | None) -> str:
    seconds = int((delta or timedelta()).total_seconds())
    sign = "+" if seconds >= 0 else "-"
    hours, rest = divmod(abs(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{sign}{hours:02d}{minutes:02d}" + (f"{secs:02d}" if secs else "")


def _transitions(zone: ZoneInfo, start: datetime, end: datetime) -> list[datetime]:
    found = []
    day = start
    while day < end:
        following = day + timedelta(days=1)
        if day.astimezone(zone).utcoffset() != following.astimezone(zone).utcoffset():
            low, high = day, following
            while high - low > timedelta(minutes=1):
                middle = low + (high - low) / 2
                if middle.astimezone(zone).utcoffset() == low.astimezone(zone).utcoffset():
                    low = middle
                else:
                    high = middle
            found.append(high.replace(second=0, microsecond=0))
        day = following
    return found


def vtimezone(timezone: str, around: datetime) -> list[str]:
    """VTIMEZONE with the real observances from one year before to one year after."""
    zone = ZoneInfo(timezone)
    start = (around - timedelta(days=370)).astimezone(UTC).replace(hour=0, minute=0, second=0)
    lines = ["BEGIN:VTIMEZONE", f"TZID:{timezone}"]
    changes = _transitions(zone, start, start + timedelta(days=740))
    if not changes:
        local = around.astimezone(zone)
        lines += [
            "BEGIN:STANDARD",
            "DTSTART:19700101T000000",
            f"TZOFFSETFROM:{_offset(local.utcoffset())}",
            f"TZOFFSETTO:{_offset(local.utcoffset())}",
            f"TZNAME:{local.tzname()}",
            "END:STANDARD",
        ]
    for change in changes:
        before = (change - timedelta(minutes=1)).astimezone(zone)
        after = change.astimezone(zone)
        kind = "DAYLIGHT" if after.dst() else "STANDARD"
        wall = (change.astimezone(UTC) + (before.utcoffset() or timedelta())).replace(tzinfo=None)
        lines += [
            f"BEGIN:{kind}",
            f"DTSTART:{wall:%Y%m%dT%H%M%S}",
            f"TZOFFSETFROM:{_offset(before.utcoffset())}",
            f"TZOFFSETTO:{_offset(after.utcoffset())}",
            f"TZNAME:{after.tzname()}",
            f"END:{kind}",
        ]
    return [*lines, "END:VTIMEZONE"]


def build_ics(event: IcsEvent) -> str:
    slot = event.slot
    tz = slot.site_timezone
    ends_at = slot.starts_at + timedelta(minutes=slot.duration_minutes)
    location = slot.site_name + (f", {slot.site_address}" if slot.site_address else "")
    cancelled = event.method == "CANCEL"
    organizer = event.organizer_name.replace('"', "'")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Orbio//Agenda de entrevistas//ES",
        "CALSCALE:GREGORIAN",
        f"METHOD:{event.method}",
        *vtimezone(tz, slot.starts_at),
        "BEGIN:VEVENT",
        f"UID:{event.uid}",
        f"SEQUENCE:{event.sequence}",
        f"DTSTAMP:{_utc(event.stamp)}",
        f"DTSTART;TZID={tz}:{_local(slot.starts_at, tz)}",
        f"DTEND;TZID={tz}:{_local(ends_at, tz)}",
        f"SUMMARY:{_escape(event.summary)}",
        f"LOCATION:{_escape(location)}",
        f"DESCRIPTION:{_escape(event.description)}",
        f'ORGANIZER;CN="{organizer}":mailto:{event.organizer_email}',
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        "TRANSP:OPAQUE",
    ]
    if not cancelled:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER:-PT1H",
            f"DESCRIPTION:{_escape(event.summary)}",
            "END:VALARM",
        ]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "".join(part + "\r\n" for line in lines for part in _fold(line))
