from time import perf_counter
from typing import Any, Literal, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from adapters.agent_langchain.middleware import record_model_usage
from screening.application.ports import MessageRecord, TraceSink
from screening.domain.errors import EvaluationFailed
from screening.domain.evals import SILENCE, Persona
from screening.domain.run_context import RunContext


class SimulatedTurn(BaseModel):
    """A structured turn keeps the persona's reasoning out of the message it sends."""

    action: Literal["reply", "silence", "end"] = Field(
        description="reply: envías message; silence: no contestas; end: la conversación terminó."
    )
    message: str = Field(description="Solo el texto que envías al asistente; vacío si no hay.")


class BedrockCandidateSimulator:
    def __init__(
        self,
        model: BaseChatModel,
        sink: TraceSink,
        model_id: str,
        effort: str,
        structured_model: Runnable[Any, Any] | None = None,
    ) -> None:
        self.model, self.sink, self.model_id, self.effort = model, sink, model_id, effort
        self.structured_model = structured_model

    async def next_message(
        self, ctx: RunContext, persona: Persona, transcript: list[MessageRecord]
    ) -> str | None:
        conversation = "\n".join(
            f"{'Asistente' if m.direction == 'out' else 'Tú'}: {m.text}" for m in transcript
        )
        simulated_name = persona.name if len(persona.name.split()) > 1 else f"{persona.name} Prueba"
        prompt = (
            "Eres una persona que busca trabajo y habla por chat con el asistente virtual de una "
            "empresa. No eres un asistente: respondes como esa persona.\n"
            f"Quién eres: {persona.name}. {persona.facts}\n"
            f"Cómo escribes: {persona.style}\nReglas:\n"
            f"- Es una simulación: si te piden nombre y apellidos, usa {simulated_name}. "
            "El apellido añadido es ficticio y solo identifica este caso de prueba.\n"
            "- En message va solo tu siguiente mensaje, tal como lo escribirías, sin comillas, "
            "notas ni razonamientos.\n"
            "- Das un dato cuando te lo preguntan o cuando encaja en la conversación, "
            "no todos de golpe.\n"
            "- Usa action=end si la conversación ha terminado (se han despedido, te han dado cita "
            "o te han pasado con una persona). Nunca uses end si el último mensaje del asistente "
            "te pregunta algo o te pide confirmar un resumen.\n"
            "- Si tus datos dicen que en un momento dejas de contestar, usa action=silence en ese "
            "momento; después actúa como digan tus datos.\n\n"
            f"# Conversación hasta ahora\n{conversation}"
        )
        structured = self.structured_model or self.model.with_structured_output(
            SimulatedTurn, method="json_schema", include_raw=True
        )
        started = perf_counter()
        output = await structured.ainvoke([HumanMessage(prompt)])
        if not isinstance(output, dict):
            raise EvaluationFailed("El simulador no ha devuelto el contenedor esperado.")
        await record_model_usage(
            ctx,
            cast(AIMessage, output["raw"]),
            self.sink,
            self.model_id,
            self.effort,
            "simulador",
            round((perf_counter() - started) * 1000),
        )
        parsed = output.get("parsed")
        if parsed is None:
            raise EvaluationFailed("No se ha podido interpretar el turno del simulador.")
        turn = parsed if isinstance(parsed, SimulatedTurn) else SimulatedTurn.model_validate(parsed)
        text = turn.message.strip()
        if turn.action == "silence":
            return SILENCE
        return None if turn.action == "end" or not text else text
