import asyncio
import json
from time import perf_counter
from typing import Any
from uuid import UUID
from weakref import WeakValueDictionary

from screening.application.faq import FaqService
from screening.application.followups import FollowupService
from screening.application.interviews import CHANGE_TOOLS, InterviewService
from screening.application.ports import (
    Assessment,
    BookingPolicy,
    CapabilityCallRecord,
    Clock,
    ConnectorRegistry,
    ConversationRepository,
    TraceSink,
)
from screening.domain.capabilities import CATALOG, TOOLS
from screening.domain.profile import ProfileContent, active_sources
from screening.domain.run_context import RunContext


class DenyBooking:
    async def can_book(self, ctx: RunContext) -> bool:
        return False

    async def booked(
        self, ctx: RunContext, profile: ProfileContent, booking: dict[str, Any]
    ) -> None:
        pass


class PendingAssessment:
    async def complete(self, ctx: RunContext) -> dict[str, str]:
        return {
            "verdict": "review",
            "next_step": "explain_review",
            "reason": "La evaluación automática todavía no está activa.",
        }


def _bounded(data: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False)
    return {"truncated": True, "preview": text[:4000]} if len(text) > 4096 else data


class CapabilityGateway:
    def __init__(
        self,
        repo: ConversationRepository,
        registry: ConnectorRegistry,
        sink: TraceSink,
        clock: Clock,
        booking: BookingPolicy,
        assessment: Assessment,
        timeout_s: float = 10.0,
    ) -> None:
        self.repo = repo
        self.registry = registry
        self.sink = sink
        self.clock = clock
        self.booking = booking
        self.assessment = assessment
        self.timeout_s = timeout_s
        self._write_locks: WeakValueDictionary[tuple[UUID, UUID], asyncio.Lock] = (
            WeakValueDictionary()
        )
        self.followups: FollowupService | None = None
        self.interviews: InterviewService | None = None
        self.faq: FaqService | None = None

    async def call(
        self, ctx: RunContext, profile: ProfileContent, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if TOOLS[tool_name].writes and ctx.session_id is not None:
            async with self._write_locks.setdefault(
                (ctx.tenant_id, ctx.session_id), asyncio.Lock()
            ):
                return await self._call(ctx, profile, tool_name, args)
        return await self._call(ctx, profile, tool_name, args)

    async def _call(
        self, ctx: RunContext, profile: ProfileContent, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        started = perf_counter()
        spec = TOOLS[tool_name]
        cap = CATALOG[spec.capability]
        status, connection_id = "ok", "platform"
        trace_error: str | None = None
        blocked = (
            spec.capability != "screening.complete" and spec.capability not in ctx.granted
        ) or (
            spec.writes
            and cap.dial_decision is not None
            and ctx.autonomy.get(cap.dial_decision, 0) == 0
        )
        blocked = blocked or ctx.agent not in cap.agents
        if not blocked and tool_name == "interview_book":
            blocked = not await self.booking.can_book(ctx) or not any(
                c.kind == "ats" and c.permission == "write" for c in profile.connections
            )
        if not blocked and tool_name in CHANGE_TOOLS:
            # Same guards as booking, but on the candidate's own active booking.
            blocked = (
                self.interviews is None
                or not await self.interviews.can_change(ctx)
                or not any(c.kind == "ats" and c.permission == "write" for c in profile.connections)
            )
        result: dict[str, Any]
        if blocked:
            status, result = "blocked", {"status": "not_allowed"}
        else:
            try:
                if spec.operation is None:
                    if tool_name == "faq_search":
                        result = (
                            await self.faq.search(
                                ctx,
                                str(args["query"]),
                                [s.id for s in active_sources(profile) if s.mode == "search"],
                            )
                            if self.faq
                            else {"status": "unavailable", "fragments": []}
                        )
                    elif tool_name == "handoff_request":
                        if ctx.session_id is None:
                            raise ValueError("La capacidad necesita una sesión.")
                        await self.repo.set_session_status(
                            ctx.tenant_id, ctx.session_id, "handed_off"
                        )
                        await self.repo.add_event(
                            ctx.tenant_id,
                            ctx.session_id,
                            "handoff_requested",
                            {"reason": args["reason"], "urgency": args.get("urgency", "normal")},
                        )
                        result = {"status": "handed_off"}
                    elif tool_name == "followup_schedule":
                        result = (
                            await self.followups.schedule(ctx, args["at_local"], args["reason"])
                            if self.followups
                            else {"status": "not_available_in_channel"}
                        )
                    else:
                        result = await self.assessment.complete(ctx)
                else:
                    connection = next(
                        (
                            c
                            for c in profile.connections
                            if spec.capability in c.backs
                            and c.kind in cap.needs_kinds
                            and (not spec.writes or c.permission == "write")
                        ),
                        None,
                    )
                    conn_args = dict(args)
                    if connection is None:
                        status, connection_id, result = "error", "", {"status": "unavailable"}
                    elif self.interviews is not None and (
                        tool_name == "interview_book" or tool_name in CHANGE_TOOLS
                    ):
                        # The agenda service owns booking records, keys, reminders and ATS stage.
                        connection_id = connection.id
                        result = await self.interviews.handle(
                            ctx, profile, connection, tool_name, args
                        )
                    else:
                        connection_id = connection.id
                        if tool_name in ("application_read_mine", "interview_book"):
                            if ctx.application_id is None:
                                raise ValueError("La capacidad necesita una candidatura.")
                            application = await self.repo.get_application(
                                ctx.tenant_id, ctx.application_id
                            )
                            if tool_name == "application_read_mine":
                                conn_args = {"external_id": application.external_id}
                            else:
                                conn_args["application_external_id"] = application.external_id
                        result = await asyncio.wait_for(
                            self.registry.get(ctx.tenant_slug, connection).call(
                                spec.operation, conn_args
                            ),
                            timeout=self.timeout_s,
                        )
                        if tool_name == "interview_book" and result.get("status") == "booked":
                            await self.repo.add_event(
                                ctx.tenant_id, ctx.session_id, "interview_booked", result
                            )
                            await self.booking.booked(ctx, profile, result)
            except Exception as exc:
                status, result, trace_error = "error", {"status": "unavailable"}, type(exc).__name__
        response = {**result, "error": trace_error} if trace_error is not None else result
        await self.sink.record_capability_call(
            CapabilityCallRecord(
                ctx.tenant_id,
                ctx.session_id,
                spec.capability,
                connection_id,
                status,
                round((perf_counter() - started) * 1000),
                _bounded({"tool": tool_name, "args": args}),
                _bounded(response),
                ctx.eval_case_id,
            )
        )
        return result
