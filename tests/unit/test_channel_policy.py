from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from screening.domain.channels import (
    DeliveryPolicyDecision,
    NormalizedInbound,
    is_opt_out,
    whatsapp_delivery,
)

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def decision(
    *,
    now: datetime = NOW,
    last_inbound_at: datetime | None = NOW,
    candidate_timezone: str = "Europe/Madrid",
    opted_out: bool = False,
    approved_template: bool = True,
    proactive: bool = True,
) -> DeliveryPolicyDecision:
    return whatsapp_delivery(
        now=now,
        last_inbound_at=last_inbound_at,
        candidate_timezone=candidate_timezone,
        opted_out=opted_out,
        approved_template=approved_template,
        proactive=proactive,
    )


@pytest.mark.parametrize(
    "elapsed,mode",
    [
        (timedelta(), "text"),
        (timedelta(hours=24, microseconds=-1), "text"),
        (timedelta(hours=24), "template"),
        (timedelta(hours=25), "template"),
    ],
)
def test_window_boundary(elapsed: timedelta, mode: str) -> None:
    assert decision(last_inbound_at=NOW - elapsed).mode == mode


def test_first_contact_requires_an_approved_template() -> None:
    assert decision(last_inbound_at=None).mode == "template"
    blocked = decision(last_inbound_at=None, approved_template=False)
    assert blocked.mode == "blocked" and blocked.reason == "no_approved_template"


def test_a_template_does_not_override_opt_out() -> None:
    blocked = decision(opted_out=True)
    assert blocked.mode == "blocked" and blocked.reason == "opted_out"


def test_future_inbound_does_not_extend_window() -> None:
    assert decision(last_inbound_at=NOW + timedelta(seconds=1)).reason == "future_inbound"


@pytest.mark.parametrize(
    "local_hour,mode", [(7, "defer"), (8, "text"), (21, "text"), (22, "defer")]
)
def test_proactive_quiet_hours(local_hour: int, mode: str) -> None:
    now = datetime(2026, 10, 23, local_hour, tzinfo=UTC)
    assert decision(now=now, last_inbound_at=now, candidate_timezone="UTC").mode == mode


def test_reactive_reply_is_not_silenced() -> None:
    now = datetime(2026, 10, 23, 23, tzinfo=UTC)
    assert decision(now=now, last_inbound_at=now, proactive=False).mode == "text"


def test_defer_uses_candidate_timezone_and_daylight_saving() -> None:
    now = datetime(2026, 3, 28, 22, tzinfo=UTC)
    result = decision(now=now, last_inbound_at=now)
    assert result.mode == "defer"
    assert result.send_at == datetime(2026, 3, 29, 6, tzinfo=UTC)


def test_service_window_uses_elapsed_time_across_clock_change() -> None:
    zone = ZoneInfo("Europe/Madrid")
    last = datetime(2026, 10, 24, 13, tzinfo=zone)
    now = datetime(2026, 10, 25, 12, tzinfo=zone)
    assert decision(now=now, last_inbound_at=last).mode == "template"


@pytest.mark.parametrize("field", ["now", "last_inbound_at"])
def test_naive_dates_are_rejected(field: str) -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        decision(**{field: NOW.replace(tzinfo=None)})


@pytest.mark.parametrize("text", ["BAJA", " stop ", "¡Baja!", "STOP."])
def test_explicit_opt_out_keywords(text: str) -> None:
    assert is_opt_out(text)


@pytest.mark.parametrize("text", ["stopper", "no quiero stop", "bajada", "hola"])
def test_other_text_is_not_an_opt_out_command(text: str) -> None:
    assert not is_opt_out(text)


def test_normalized_message_requires_provider_identity_and_aware_time() -> None:
    with pytest.raises(ValueError, match="identidad"):
        NormalizedInbound(uuid4(), "whatsapp", "", "synthetic", "hola", NOW)
    with pytest.raises(ValueError, match="zona horaria"):
        NormalizedInbound(
            uuid4(), "whatsapp", "test-id", "synthetic", "hola", NOW.replace(tzinfo=None)
        )
