import base64
import math
import struct
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from adapters.channels.twilio_voice import (
    CallOutcomeUnknown,
    TelephoneCodec,
    TwilioVoiceClient,
    TwilioVoiceConfig,
)
from screening.domain.errors import NotAllowed

ACCOUNT = "AC" + "1" * 32
CALL = "CA" + "2" * 32
PHONE = "+34600000001"
SENDER = "+12025550101"


def configuration(**overrides: object) -> TwilioVoiceConfig:
    return TwilioVoiceConfig.model_validate(
        {
            "tenant_id": str(uuid4()),
            "account_sid": ACCOUNT,
            "auth_token": "synthetic-only-test-secret",
            "sender": SENDER,
            "participants": [PHONE],
            "public_base_url": "https://voice.example",
            "enabled": True,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            **overrides,
        }
    )


def preflight(request: httpx.Request, account_type: str = "Full") -> httpx.Response:
    if request.url.path.endswith(f"{ACCOUNT}.json"):
        return httpx.Response(200, json={"sid": ACCOUNT, "type": account_type, "status": "active"})
    assert request.url.params["PhoneNumber"] == SENDER
    return httpx.Response(
        200,
        json={
            "incoming_phone_numbers": [
                {"account_sid": ACCOUNT, "phone_number": SENDER, "capabilities": {"voice": True}}
            ]
        },
    )


def accepted(**overrides: object) -> dict[str, object]:
    return {"sid": CALL, "account_sid": ACCOUNT, "to": PHONE, "from": SENDER, **overrides}


@pytest.mark.parametrize(
    "changes",
    [
        {"participants": ["+34900000001"]},
        {"participants": ["+34700000001"]},
        {"participants": ["+441234567890"]},
        {"participants": [f"+34600000{i:03d}" for i in range(11)]},
        {"sender": "+34600000001"},
        {"max_seconds": 301},
        {"expires_at": "2026-10-27T12:00:00"},
        {"public_base_url": "http://voice.example"},
        {"public_base_url": "https://name:secret@voice.example"},
        {"public_base_url": "https://voice.example/?secret=private"},
        {"public_base_url": "https://voice.example:8080"},
        {"public_base_url": "https://voice.example/other"},
    ],
)
def test_configuration_fails_closed(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        configuration(**changes)


def test_callbacks_are_scoped_and_default_disabled() -> None:
    cfg = configuration(enabled=False)
    assert not cfg.active
    attempt = uuid4()
    expected = f"/api/v1/webhooks/twilio-voice/{cfg.tenant_id}/{attempt}"
    assert cfg.callback(attempt, "answer") == "https://voice.example" + expected + "/answer"
    assert cfg.callback(attempt, "media") == "wss://voice.example" + expected + "/media"
    with pytest.raises(ValueError):
        cfg.callback(attempt, "https://other.example")


@pytest.mark.parametrize(
    "changes, contact",
    [
        ({"enabled": False}, PHONE),
        ({"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, PHONE),
        ({}, "+34900000002"),
        ({}, "+34700000002"),
        ({}, "+442071234567"),
        ({}, "112"),
        ({}, "+3460000000１"),
    ],
)
async def test_invalid_permission_never_touches_network(
    changes: dict[str, object], contact: str
) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        pytest.fail("Network must not be touched")

    with pytest.raises(NotAllowed):
        await TwilioVoiceClient(configuration(**changes), httpx.MockTransport(forbidden)).dial(
            contact, uuid4()
        )


@pytest.mark.parametrize("failure", ["Trial", "suspended", "no_number", "wrong_account"])
async def test_preflight_rejects_account_and_number_before_post(failure: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if failure == "Trial":
            return preflight(request, "Trial")
        if failure == "suspended":
            return httpx.Response(200, json={"sid": ACCOUNT, "type": "Full", "status": "suspended"})
        if failure == "wrong_account":
            return httpx.Response(
                200, json={"sid": "AC" + "3" * 32, "type": "Full", "status": "active"}
            )
        if "IncomingPhoneNumbers" in request.url.path:
            return httpx.Response(200, json={"incoming_phone_numbers": []})
        return preflight(request)

    with pytest.raises(NotAllowed):
        await TwilioVoiceClient(configuration(), httpx.MockTransport(handler)).dial(PHONE, uuid4())


async def test_single_post_five_minutes_no_recording_fixed_callbacks() -> None:
    posts = []
    attempt = uuid4()
    cfg = configuration(participants=[])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.twilio.com"
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "GET":
            return preflight(request)
        posts.append(request)
        form = parse_qs(request.content.decode())
        assert form["To"] == [PHONE] and form["From"] == [SENDER]
        assert form["TimeLimit"] == ["300"]
        assert form["Timeout"] == ["15"]
        assert form["Record"] == ["false"]
        assert form["Url"] == [cfg.callback(attempt, "answer")]
        assert form["StatusCallback"] == [cfg.callback(attempt, "status")]
        assert form["Method"] == form["StatusCallbackMethod"] == ["POST"]
        return httpx.Response(201, json=accepted(status="queued"))

    assert await TwilioVoiceClient(cfg, httpx.MockTransport(handler)).dial(PHONE, attempt) == CALL
    assert len(posts) == 1  # Accepted is not a claim of answered/delivered/audio.


async def test_call_window_ending_during_preflight_prevents_post(monkeypatch) -> None:
    from adapters.channels import twilio_voice

    deadline = datetime.now(UTC) + timedelta(seconds=1)
    current = deadline - timedelta(seconds=1)
    cfg = configuration()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current

    monkeypatch.setattr(twilio_voice, "datetime", Clock)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal current
        assert request.method == "GET", "No call after the candidate's quiet hours start"
        response = preflight(request)
        if "IncomingPhoneNumbers" in request.url.path:
            current = deadline
        return response

    with pytest.raises(NotAllowed, match="horario"):
        await TwilioVoiceClient(cfg, httpx.MockTransport(handler)).dial(
            PHONE, uuid4(), not_after=deadline
        )


@pytest.mark.parametrize(
    "outcome", ["timeout", "500", "redirect", "invalid_sid", "wrong_to", "invalid_json"]
)
async def test_unknown_call_never_retries_or_leaks(outcome: str) -> None:
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return preflight(request)
        posts.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("private-details")
        if outcome == "500":
            return httpx.Response(500, text="private-details")
        if outcome == "redirect":
            return httpx.Response(302, headers={"location": "https://other.example"})
        if outcome == "invalid_json":
            return httpx.Response(201, text="private-details")
        return httpx.Response(
            201,
            json=accepted(
                **({"sid": "invalid"} if outcome == "invalid_sid" else {"to": "+34600000002"})
            ),
        )

    with pytest.raises(CallOutcomeUnknown) as failure:
        await TwilioVoiceClient(configuration(), httpx.MockTransport(handler)).dial(PHONE, uuid4())
    assert len(posts) == 1
    assert "private-details" not in str(failure.value) and PHONE not in str(failure.value)


@pytest.mark.parametrize("status", [400, 401, 403, 429])
async def test_explicit_rejection_never_retries(status: int) -> None:
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return preflight(request)
        posts.append(request)
        return httpx.Response(status, text="private-details")

    with pytest.raises(NotAllowed) as failure:
        await TwilioVoiceClient(configuration(), httpx.MockTransport(handler)).dial(PHONE, uuid4())
    assert len(posts) == 1 and "private-details" not in str(failure.value)


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_codec_silence_and_state_across_frames() -> None:
    inbound = bytes([255]) * 1600
    whole = TelephoneCodec().to_agent(encode(inbound))
    codec = TelephoneCodec()
    framed = b"".join(
        base64.b64decode(codec.to_agent(encode(inbound[i : i + 160])))
        for i in range(0, len(inbound), 160)
    )
    assert framed == base64.b64decode(whole)
    assert not any(framed)
    assert 6396 <= len(framed) <= 6400


def test_codec_tone_roundtrip_and_streaming_match() -> None:
    samples = [int(10000 * math.sin(2 * math.pi * 440 * n / 16000)) for n in range(16000)]
    pcm = struct.pack("<16000h", *samples)
    whole = TelephoneCodec().to_phone(encode(pcm))
    codec = TelephoneCodec()
    framed = b"".join(
        base64.b64decode(codec.to_phone(encode(pcm[i : i + 640]))) for i in range(0, len(pcm), 640)
    )
    assert framed == base64.b64decode(whole)
    assert len(framed) == 8000
    restored = base64.b64decode(TelephoneCodec().to_agent(whole))
    decoded = struct.unpack(f"<{len(restored) // 2}h", restored)
    rms = math.sqrt(sum(x * x for x in decoded) / len(decoded))
    assert 6000 < rms < 8000  # Nonzero, bounded audio; not a speaker-quality assertion.
    crossings = sum(a < 0 <= b for a, b in pairwise(decoded))
    assert 438 <= crossings <= 441


@pytest.mark.parametrize("payload", ["bad-base64", "", encode(b"x" * 8001)])
def test_inbound_audio_rejects_bad_or_oversized_frames(payload: str) -> None:
    with pytest.raises(ValueError):
        TelephoneCodec().to_agent(payload)


@pytest.mark.parametrize("payload", ["bad-base64", "", encode(b"x"), encode(b"x" * 160002)])
def test_outbound_audio_rejects_bad_or_oversized_frames(payload: str) -> None:
    with pytest.raises(ValueError):
        TelephoneCodec().to_phone(payload)
