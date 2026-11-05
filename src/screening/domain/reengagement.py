"""When the platform may remind a silent candidate. Pure functions over the FDE policy."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from screening.domain.profile import ReengagementPolicy

# Platform floor: never contact candidates between 22:00 and 08:00 local time,
# whatever the client's policy says. A policy may only be stricter.
PLATFORM_QUIET = (22, 8)


def _quiet(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    return start <= hour or hour < end if start > end else start <= hour < end


def is_quiet(at: datetime, timezone: str, policy: ReengagementPolicy) -> bool:
    hour = at.astimezone(ZoneInfo(timezone)).hour
    return _quiet(hour, *PLATFORM_QUIET) or _quiet(
        hour, policy.quiet_from_hour, policy.quiet_until_hour
    )


def next_allowed(at: datetime, timezone: str, policy: ReengagementPolicy) -> datetime:
    """The first instant at or after `at` outside both quiet windows."""
    candidate = at.astimezone(UTC)
    for _ in range(48):
        if not is_quiet(candidate, timezone, policy):
            return candidate
        local = candidate.astimezone(ZoneInfo(timezone))
        candidate = (
            local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        ).astimezone(UTC)
    raise ValueError("La política de silencio no deja ninguna hora disponible.")


def reminder_due(
    policy: ReengagementPolicy, attempt: int, reference: datetime, timezone: str
) -> datetime:
    """Attempt 1 waits `first_after_minutes`; later ones go to the next local morning."""
    if attempt <= 1:
        return next_allowed(
            reference + timedelta(minutes=policy.first_after_minutes), timezone, policy
        )
    local = reference.astimezone(ZoneInfo(timezone))
    morning_hour = max(policy.quiet_until_hour, PLATFORM_QUIET[1]) + 1
    morning = datetime.combine(local.date() + timedelta(days=1), time(min(morning_hour, 12)))
    earliest = reference + timedelta(minutes=policy.first_after_minutes)
    due = max(morning.replace(tzinfo=local.tzinfo).astimezone(UTC), earliest)
    return next_allowed(due, timezone, policy)


def abandon_due(policy: ReengagementPolicy, last_reminder: datetime) -> datetime:
    return last_reminder + timedelta(hours=policy.abandon_after_hours)
