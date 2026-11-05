import logging
from time import perf_counter
from typing import cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from adapters.agent_langchain.middleware import record_model_usage
from screening.application.ports import CapabilityCallRecord, GuardVerdict, TraceSink
from screening.domain.run_context import RunContext

logger = logging.getLogger(__name__)

GUARD_PROMPT = (
    "Eres el guardián de una plataforma de cribado. Revisas un mensaje que un agente quiere "
    "enviar a un candidato. Lo apruebas salvo que incumpla alguna de estas reglas:\n"
    "1. No pregunta ni registra edad, nacionalidad, origen, salud, discapacidad, embarazo, "
    "religión, orientación sexual ni afiliación política o sindical.\n"
    "2. No promete sueldos, horarios ni condiciones que no estén en las fuentes del cliente.\n"
    "3. No comparte información de otras personas ni de otras candidaturas.\n"
    "Los resultados de herramientas autorizadas también son fuentes válidas: no rechaces "
    "distancias, vacantes o citas por no figurar en el manual si constan en esos resultados. "
    "No extiendas su alcance más allá de los campos devueltos por el sistema. "
    "Interpreta las fechas relativas con el reloj verificado proporcionado.\n"
    "Si lo rechazas, explica en una frase qué regla incumple.\n\n"
    "# Fuentes del cliente\n{sources}\n\n# Mensaje propuesto\n{reply}"
)


class _GuardOutput(BaseModel):
    approved: bool
    reason: str


class BedrockReplyGuard:
    def __init__(
        self,
        model: BaseChatModel,
        sink: TraceSink,
        model_id: str,
        effort: str,
        prompt: str = GUARD_PROMPT,
    ) -> None:
        self.model, self.sink, self.model_id, self.effort = model, sink, model_id, effort
        self.prompt = prompt

    async def review(self, ctx: RunContext, reply: str, sources: str) -> GuardVerdict:
        started = perf_counter()
        output = await self.model.with_structured_output(
            _GuardOutput, method="json_schema", include_raw=True
        ).ainvoke([HumanMessage(self.prompt.format(sources=sources, reply=reply))])
        if not isinstance(output, dict):
            raise TypeError("El guardián no ha devuelto el contenedor include_raw.")
        raw = cast(AIMessage, output["raw"])
        await record_model_usage(
            ctx,
            raw,
            self.sink,
            self.model_id,
            self.effort,
            "guardian",
            round((perf_counter() - started) * 1000),
        )
        parsed = output["parsed"]
        if parsed is None:
            logger.warning("El guardián no ha producido un veredicto estructurado")
            return GuardVerdict(approved=False, reason="sin veredicto")
        verdict = cast(_GuardOutput, parsed)
        if not verdict.approved:
            await self.sink.record_capability_call(
                CapabilityCallRecord(
                    tenant_id=ctx.tenant_id,
                    session_id=ctx.session_id,
                    capability="platform.reply_guard",
                    connection_id="platform",
                    status="blocked",
                    latency_ms=round((perf_counter() - started) * 1000),
                    request={},
                    response={"reason": verdict.reason[:300]},
                    eval_case_id=ctx.eval_case_id,
                )
            )
        return GuardVerdict(approved=verdict.approved, reason=verdict.reason)
