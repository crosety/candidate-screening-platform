from screening.application.ports import EvaluationRequest
from screening.domain.result import EvaluationOutput


class ScriptedEvaluator:
    def __init__(self, outputs: list[EvaluationOutput | Exception]) -> None:
        if not outputs:
            raise ValueError("Falta una salida para el evaluador de prueba.")
        self.outputs = list(outputs)
        self.requests: list[EvaluationRequest] = []

    async def evaluate(self, request: EvaluationRequest) -> EvaluationOutput:
        self.requests.append(request)
        output = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(output, Exception):
            raise output
        return output.model_copy(deep=True)
