import asyncio
from time import perf_counter
from typing import Any

from screening.application.ports import CapabilityCallRecord, TraceSink
from screening.application.recruiter import (
    AggregateQuery,
    CandidateQuery,
    RecruiterReader,
    RecruiterScope,
)
from screening.domain.errors import NotAllowed
from screening.domain.run_context import RunContext


class RecruiterQueryGateway:
    """Read-only capability boundary with immutable scope, timeout and minimized audit."""

    def __init__(
        self, ctx: RunContext, scope: RecruiterScope, reader: RecruiterReader, sink: TraceSink
    ):
        if (
            ctx.agent != "recruiter"
            or ctx.tenant_id != scope.tenant_id
            or ctx.allowed_segments != scope.segments
        ):
            raise NotAllowed("Alcance de selección no válido.")
        self.ctx, self.scope, self.reader, self.sink = ctx, scope, reader, sink

    async def call(self, capability: str, args: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        status = "blocked"
        try:
            if capability not in self.ctx.granted:
                raise NotAllowed("Capacidad no autorizada.")
            async with asyncio.timeout(10):
                if capability == "candidates.query":
                    result = await self.reader.query(
                        self.scope, CandidateQuery.model_validate(args)
                    )
                elif capability == "analytics.aggregates":
                    result = await self.reader.aggregates(
                        self.scope, AggregateQuery.model_validate(args)
                    )
                else:
                    raise NotAllowed("Capacidad no disponible.")
            status = "ok"
            return result
        except TimeoutError:
            status = "error"
            return {"status": "timeout"}
        finally:
            await self.sink.record_capability_call(
                CapabilityCallRecord(
                    self.ctx.tenant_id,
                    self.ctx.session_id,
                    capability,
                    "recruiter_read",
                    status,
                    round((perf_counter() - started) * 1000),
                    {"filter_keys": sorted(args), "segments": list(self.scope.segments)},
                    {"status": status},
                    self.ctx.eval_case_id,
                )
            )
