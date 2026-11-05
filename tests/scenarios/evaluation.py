from datetime import UTC, datetime
from uuid import UUID

from adapters.agent_langchain.evaluator import BedrockEvaluator
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.ports import EvaluationRequest, MessageRecord
from screening.domain.result import quoted_in
from screening.domain.run_context import RunContext
from tests.scenarios.legacy_openai import (
    RECORDED_EFFORT,
    RECORDED_MODEL_ID,
    recorded_luna_model,
)
from tests.scenarios.openai_conversation import load_profile


def transcript(lines: list[tuple[str, str]]) -> list[MessageRecord]:
    return [
        MessageRecord(UUID(int=i + 1), direction, text, datetime(2026, 10, 22, tzinfo=UTC))
        for i, (direction, text) in enumerate(lines)
    ]


LUCIA = transcript(
    [
        ("out", "Soy Sofía, asistente virtual de Grupo Sazón. ¿Cómo te llamas y dónde vives?"),
        ("in", "Soy Lucía Martín, vivo en Benimaclet, València."),
        ("out", "Hay vacantes de tarde en Sazón Benimaclet, a un minuto. ¿Tienes carné de moto?"),
        ("in", "Tengo el carné A2 en vigor desde hace cuatro años y he repartido con Glovo."),
        ("out", "¿Qué jornada y turno buscas?"),
        ("in", "Quiero media jornada por las tardes."),
        ("out", "¿Cuándo podrías incorporarte?"),
        ("in", "Puedo empezar la semana que viene."),
        (
            "out",
            "Entonces: A2, experiencia en Glovo, media jornada de tarde en Benimaclet "
            "y disponible la semana que viene. ¿Correcto?",
        ),
        ("in", "Sí, todo correcto, confirmo el resumen."),
    ]
)
ANDRES = transcript(
    [
        ("out", "Soy Sofía, asistente virtual de Grupo Sazón. ¿Cómo te llamas?"),
        ("in", "Andrés Gil."),
        ("out", "¿Dónde vives?"),
        ("in", "Vivo en Ruzafa, València."),
        ("out", "Para repartir en moto necesitamos carné. ¿Cuál tienes?"),
        ("in", "No tengo carné de moto ni de coche y no pienso sacármelo."),
        ("out", "¿Qué jornada y disponibilidad tienes?"),
        ("in", "Completa, cualquier turno, puedo empezar mañana."),
        (
            "out",
            "Confirmo: Ruzafa, jornada completa, cualquier turno y sin carné "
            "ni intención de obtenerlo. ¿Es correcto?",
        ),
        ("in", "Sí, confirmo todo."),
    ]
)


async def exercise() -> InMemoryTraceSink:
    sink = InMemoryTraceSink()
    evaluator = BedrockEvaluator(
        recorded_luna_model("evaluador"), sink, RECORDED_MODEL_ID, RECORDED_EFFORT["evaluador"]
    )
    ctx = RunContext(
        "evaluador",
        UUID(int=1),
        "grupo_sazon",
        "tenant",
        "nobody",
        None,
        None,
        None,
        frozenset(),
        {},
    )
    lucia = await evaluator.evaluate(EvaluationRequest(ctx, load_profile(), LUCIA))
    assert lucia.verdict == "qualified", lucia.model_dump()
    assert lucia.fields["carnet"].value is True
    evidence = lucia.fields["carnet"].evidence
    assert quoted_in(evidence, [message.text for message in LUCIA if message.direction == "in"])
    andres = await evaluator.evaluate(EvaluationRequest(ctx, load_profile(), ANDRES))
    assert andres.verdict == "not_qualified", andres.model_dump()
    assert andres.fields["carnet"].value is False
    return sink
