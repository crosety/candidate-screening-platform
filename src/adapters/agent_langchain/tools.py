import json
from typing import Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from adapters.agent_langchain.context import AgentContext

DESCRIPTIONS = {
    "faq_search": "Busca evidencia en FAQ de esta versión. Sin resultados, no inventes respuestas.",
    "openings_find": (
        "Busca las oportunidades con vacantes más cercanas al lugar que diga el candidato. "
        "Devuelve hasta 3, ordenadas por minutos de trayecto. Si el lugar es ambiguo, "
        "devuelve las opciones para que preguntes cuál es. "
        "Describe únicamente los campos devueltos; recoge la preferencia "
        "del candidato por separado, sin presentarla como condición confirmada de la vacante."
    ),
    "application_read_mine": (
        "Lee los datos de la candidatura de la persona con la que hablas: nombre, ciudad y puesto."
    ),
    "interview_slots": (
        "Da los 3 próximos huecos libres de entrevista en una oportunidad "
        "(el site_id de openings_find)."
    ),
    "interview_book": (
        "Reserva un hueco de entrevista (el slot_id de interview_slots). Solo funciona "
        "cuando la plataforma ya ha marcado a la persona como apta."
    ),
    "interview_reschedule": (
        "Cambia la entrevista ya reservada de esta persona a otro hueco libre (el slot_id de "
        "interview_slots). Úsala solo si el candidato ha pedido cambiarla y ha elegido "
        "expresamente ese hueco. Confirma después lo que indique confirmation."
    ),
    "interview_cancel": (
        "Cancela la entrevista ya reservada de esta persona. Úsala solo si el candidato ha "
        "pedido cancelarla y lo ha confirmado de forma explícita. No acepta identificadores: "
        "siempre actúa sobre su propia entrevista."
    ),
    "handoff_request": (
        "Pasa la conversación a una persona del equipo. "
        "Úsala si el candidato lo pide o si no puedes ayudarle."
    ),
    "credential_verify": "Comprueba un certificado o carné por su número en el registro oficial.",
    "followup_schedule": "Programa un mensaje de seguimiento para más tarde.",
    "screening_complete": (
        "Llámala cuando tengas todos los datos que pide el brief y el candidato haya "
        "confirmado tu resumen. Devuelve el veredicto y el siguiente paso."
    ),
}


class NoArgs(BaseModel):
    pass


class OpeningsFindArgs(BaseModel):
    near: str = Field(
        min_length=1, max_length=120, description="Barrio, pueblo o lugar que dice el candidato"
    )
    shift: Literal["morning", "afternoon", "night", "rotating", "any"] = "any"


class InterviewSlotsArgs(BaseModel):
    site_id: str


class InterviewBookArgs(BaseModel):
    slot_id: str


class InterviewRescheduleArgs(BaseModel):
    slot_id: str = Field(min_length=1, max_length=60, description="slot_id del nuevo hueco elegido")


class HandoffRequestArgs(BaseModel):
    reason: str = Field(min_length=1, max_length=300)
    urgency: Literal["normal", "high"] = "normal"


class CredentialVerifyArgs(BaseModel):
    number: str = Field(min_length=1, max_length=40)


class FollowupScheduleArgs(BaseModel):
    at_local: str
    reason: str


class FaqSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)


@tool("faq_search", args_schema=FaqSearchArgs, description=DESCRIPTIONS["faq_search"])
async def faq_search(query: str, *, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "faq_search", {"query": query})


async def _call(runtime: ToolRuntime[AgentContext], name: str, args: dict[str, str]) -> str:
    result = await runtime.context.gateway.call(
        runtime.context.run_context, runtime.context.profile, name, args
    )
    return json.dumps(result, ensure_ascii=False)


@tool("openings_find", args_schema=OpeningsFindArgs, description=DESCRIPTIONS["openings_find"])
async def openings_find(
    near: str, shift: str = "any", *, runtime: ToolRuntime[AgentContext]
) -> str:
    return await _call(runtime, "openings_find", {"near": near, "shift": shift})


@tool(
    "application_read_mine",
    args_schema=NoArgs.model_json_schema(),
    description=DESCRIPTIONS["application_read_mine"],
)
async def application_read_mine(*, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "application_read_mine", {})


@tool(
    "interview_slots", args_schema=InterviewSlotsArgs, description=DESCRIPTIONS["interview_slots"]
)
async def interview_slots(site_id: str, *, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "interview_slots", {"site_id": site_id})


@tool("interview_book", args_schema=InterviewBookArgs, description=DESCRIPTIONS["interview_book"])
async def interview_book(slot_id: str, *, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "interview_book", {"slot_id": slot_id})


@tool(
    "interview_reschedule",
    args_schema=InterviewRescheduleArgs,
    description=DESCRIPTIONS["interview_reschedule"],
)
async def interview_reschedule(slot_id: str, *, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "interview_reschedule", {"slot_id": slot_id})


# A JSON schema avoids LangChain's empty-Pydantic shortcut, which drops injected runtime.
@tool(
    "interview_cancel",
    args_schema=NoArgs.model_json_schema(),
    description=DESCRIPTIONS["interview_cancel"],
)
async def interview_cancel(*, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "interview_cancel", {})


@tool(
    "handoff_request", args_schema=HandoffRequestArgs, description=DESCRIPTIONS["handoff_request"]
)
async def handoff_request(
    reason: str, urgency: str = "normal", *, runtime: ToolRuntime[AgentContext]
) -> str:
    return await _call(runtime, "handoff_request", {"reason": reason, "urgency": urgency})


@tool(
    "credential_verify",
    args_schema=CredentialVerifyArgs,
    description=DESCRIPTIONS["credential_verify"],
)
async def credential_verify(number: str, *, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "credential_verify", {"number": number})


@tool(
    "followup_schedule",
    args_schema=FollowupScheduleArgs,
    description=DESCRIPTIONS["followup_schedule"],
)
async def followup_schedule(
    at_local: str, reason: str, *, runtime: ToolRuntime[AgentContext]
) -> str:
    return await _call(runtime, "followup_schedule", {"at_local": at_local, "reason": reason})


# A JSON schema avoids LangChain's empty-Pydantic shortcut, which drops injected runtime.
@tool(
    "screening_complete",
    args_schema=NoArgs.model_json_schema(),
    description=DESCRIPTIONS["screening_complete"],
)
async def screening_complete(*, runtime: ToolRuntime[AgentContext]) -> str:
    return await _call(runtime, "screening_complete", {})


ALL_TOOLS: dict[str, BaseTool] = {
    t.name: t
    for t in (
        faq_search,
        openings_find,
        application_read_mine,
        interview_slots,
        interview_book,
        interview_reschedule,
        interview_cancel,
        handoff_request,
        credential_verify,
        followup_schedule,
        screening_complete,
    )
}


def build_tools(names: list[str]) -> list[BaseTool]:
    return [ALL_TOOLS[name] for name in names]
