from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from asgiref.sync import sync_to_async
from django.db import connections
from django.test import Client

from screening.application.ports import GuardVerdict
from screening.domain.capabilities import CATALOG
from screening.platform.container import get_container
from screening.platform.voice_runtime import VoiceRuntime
from tests.api.test_phone_api import phone
from web.accounts.models import User
from web.conversations.models import Session
from web.tenancy.context import tenant_context

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest_asyncio.fixture(autouse=True)
async def close_async_database_connections():
    yield
    await sync_to_async(connections.close_all)()


async def runtime(phone: tuple[Client, UUID, UUID]) -> VoiceRuntime:
    _, tenant, app = phone
    c = get_container()
    token = await c.conversations.repo.get_or_create_link(tenant, app)
    opened = await c.conversations.open_session(token)
    session = await c.conversations.repo.get_session(tenant, opened.session_id)
    r = VoiceRuntime.__new__(VoiceRuntime)
    r.session = replace(
        session, run_context=replace(session.run_context, granted=frozenset(CATALOG))
    )
    r.service = c.conversations
    r.c = SimpleNamespace(
        clock=c.clock, gateway=SimpleNamespace(call=AsyncMock(return_value={"ok": True}))
    )
    r.guard = SimpleNamespace(
        review=AsyncMock(return_value=GuardVerdict(approved=True, reason="tested"))
    )
    return r


@pytest.mark.parametrize(
    "name,args",
    [
        ("faq_search", {"query": "Horario"}),
        ("openings_find", {"near": "Valencia"}),
        ("application_read_mine", {}),
        ("interview_slots", {"site_id": "site"}),
        ("interview_book", {"slot_id": "slot"}),
        ("handoff_request", {"reason": "asistencia humana"}),
        ("credential_verify", {"number": "CERT-1"}),
        ("followup_schedule", {"at_local": "2026-10-28T10:00:00", "reason": "continuar"}),
        ("screening_complete", {}),
    ],
)
async def test_every_tool_waits_for_guard_before_execution(phone, name, args):
    r = await runtime(phone)
    order = []

    async def guard(*a):
        order.append("guard")
        return GuardVerdict(approved=True, reason="safe")

    async def call(*a):
        order.append("execute")
        return {"ok": True}

    r.guard.review.side_effect = guard
    r.c.gateway.call.side_effect = call
    assert await r.tool("once", name, args) == {"ok": True}
    assert order == ["guard", "execute"]
    assert (await r.tool("once", name, args))["error"] == "already_attempted"
    assert order.count("execute") == 1


async def test_denial_timeout_invalid_and_ungranted_never_execute(phone):
    r = await runtime(phone)
    r.guard.review.return_value = GuardVerdict(approved=False, reason="injection")
    assert (await r.tool("blocked", "interview_book", {"slot_id": "other-person"}))[
        "error"
    ] == "guard_blocked"
    r.guard.review.side_effect = TimeoutError
    assert (await r.tool("timeout", "screening_complete", {}))["error"] == "guard_timeout"
    assert (await r.tool("extra", "application_read_mine", {"candidate": "someone"}))[
        "error"
    ] == "unexpected_arguments"
    assert (await r.tool("invalid", "openings_find", {"near": ""}))["error"] == "invalid_arguments"
    r.session = replace(r.session, run_context=replace(r.session.run_context, granted=frozenset()))
    assert (await r.tool("none", "interview_book", {"slot_id": "x"}))["error"] == "tool_not_allowed"
    r.c.gateway.call.assert_not_awaited()


async def test_candidate_correction_invalidates_pending_action(phone):
    r = await runtime(phone)

    async def guard(*a):
        await r.service.repo.add_message(
            r.session.tenant_id, r.session.id, "in", "No, prefiero mañana", "correction"
        )
        return GuardVerdict(approved=True, reason="old evidence")

    r.guard.review.side_effect = guard
    assert (await r.tool("stale", "interview_book", {"slot_id": "today"}))[
        "error"
    ] == "candidate_updated_answer"
    r.c.gateway.call.assert_not_awaited()


async def test_hr_takeover_blocks_pending_tools_and_late_transcripts(phone):
    r = await runtime(phone)

    @sync_to_async
    def claim():
        with tenant_context(r.session.tenant_id):
            Session.objects.filter(pk=r.session.id).update(
                status="handed_off", human_owner=User.objects.get(email="reclutadora@orbio.local")
            )

    async def guard(*a):
        await claim()
        return GuardVerdict(approved=True, reason="stale approval")

    r.guard.review.side_effect = guard
    assert (await r.tool("pending", "interview_book", {"slot_id": "x"}))[
        "error"
    ] == "session_not_active"
    assert await r.transcript("out", "AI must stop", "late") is None
    assert await r.transcript("in", "No longer in voice", "late-in") is None
    r.c.gateway.call.assert_not_awaited()


async def test_transcript_idempotency_and_history(phone):
    r = await runtime(phone)
    assert await r.transcript("in", "Hola", "event1")
    assert await r.transcript("in", "Hola", "event1") is None
    assert len(await r.service.repo.load_history(r.session.tenant_id, r.session.id)) == 1


async def test_pause_keeps_application_open_and_does_not_send_sms(phone):
    r = await runtime(phone)
    r.transport = "telephone"
    result = await r.tool("pause-one", "voice_pause", {})
    assert result["status"] == "paused" and result["sms_sent"] is False
    assert (await r.service.repo.get_session(r.session.tenant_id, r.session.id)).status == "active"
    assert r.pause_requested
    r.guard.review.assert_awaited_once()
    r.c.gateway.call.assert_not_awaited()
    assert (await r.tool("late", "screening_complete", {}))["error"] == "voice_paused"


async def test_pause_guard_denial_leaves_call_and_application_unchanged(phone):
    r = await runtime(phone)
    r.guard.review.return_value = GuardVerdict(approved=False, reason="not requested")
    assert (await r.tool("pause-denied", "voice_pause", {}))["error"] == "guard_blocked"
    assert not getattr(r, "pause_requested", False)
    assert (await r.service.repo.get_session(r.session.tenant_id, r.session.id)).status == "active"
    r.c.gateway.call.assert_not_awaited()


async def test_telephone_then_browser_then_text_keep_same_history(phone):
    r = await runtime(phone)
    r.transport = "telephone"
    phone_message = await r.transcript("in", "Puedo trabajar los fines de semana", "phone-1")
    assert phone_message.channel == "telephone"
    r.transport = "browser"
    web_message = await r.transcript("out", "Retomamos desde tu disponibilidad", "browser-1")
    assert web_message.channel == "voice"
    history = await r.service.repo.load_history(r.session.tenant_id, r.session.id)
    assert len(history) == 2
    assert "fines de semana" in str(history[0].content)
