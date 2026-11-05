from screening.application.ports import SerializedMessage, TurnRequest, TurnResult
from screening.application.prompts import FIRST_MESSAGE


class EchoAgentRunner:
    async def run_turn(self, request: TurnRequest) -> TurnResult:
        reply = (
            "Hola, soy el asistente virtual en modo de prueba, sin modelo. "
            "Escribe algo y te lo repetiré."
            if request.text is None
            else f"(modo de prueba) He recibido: {request.text}"
        )
        return TurnResult(
            [
                SerializedMessage(
                    "user",
                    {
                        "type": "fake",
                        "text": request.text if request.text is not None else FIRST_MESSAGE,
                    },
                ),
                SerializedMessage("assistant", {"type": "fake", "text": reply}),
            ],
            reply,
            frozenset(),
        )
