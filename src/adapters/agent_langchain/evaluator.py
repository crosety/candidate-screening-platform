from copy import deepcopy
from time import perf_counter
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from adapters.agent_langchain.middleware import record_model_usage
from screening.application.ports import EvaluationRequest, TraceSink
from screening.application.prompts import EVALUATOR_INSTRUCTIONS, build_evaluation_input
from screening.domain.errors import EvaluationFailed
from screening.domain.profile import ProfileContent
from screening.domain.result import EvaluationOutput, FieldValue


def evaluation_schema(profile: ProfileContent) -> dict[str, Any]:
    # Strict Responses schemas need named properties, not arbitrary dict keys.
    # The profile still owns the fields; this is only the provider wire schema.
    schema = EvaluationOutput.model_json_schema()
    schema["additionalProperties"] = False
    schema["$defs"]["FieldValue"]["additionalProperties"] = False
    schema["$defs"]["StageSentiment"]["additionalProperties"] = False
    schema["required"] = list(schema["properties"])
    properties = {}
    for field in profile.result_schema:
        item = deepcopy(schema["$defs"]["FieldValue"])
        kind = {
            "text": "string",
            "enum": "string",
            "date": "string",
            "boolean": "boolean",
            "number": "number",
        }[field.value_type]
        value: dict[str, Any] = {"type": kind}
        if field.value_type == "enum":
            value["enum"] = field.choices
        item["properties"]["value"] = {"anyOf": [value, {"type": "null"}]}
        properties[field.key] = item
    schema["properties"]["fields"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": [field.key for field in profile.result_schema],
    }
    return schema


class BedrockEvaluator:
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

    async def evaluate(self, request: EvaluationRequest) -> EvaluationOutput:
        structured = self.structured_model
        if structured is None:
            structured = self.model.with_structured_output(
                evaluation_schema(request.profile), method="json_schema", include_raw=True
            )
        started = perf_counter()
        output = await structured.ainvoke(
            [
                SystemMessage(EVALUATOR_INSTRUCTIONS),
                HumanMessage(
                    build_evaluation_input(
                        request.profile, request.transcript, request.tool_evidence
                    )
                ),
            ]
        )
        if not isinstance(output, dict):
            raise EvaluationFailed("El evaluador no ha devuelto el contenedor esperado.")
        await record_model_usage(
            request.run_context,
            cast(AIMessage, output["raw"]),
            self.sink,
            self.model_id,
            self.effort,
            "evaluador",
            round((perf_counter() - started) * 1000),
        )
        if output.get("parsed") is None:
            raise EvaluationFailed("No se ha podido interpretar el resultado del evaluador.")
        try:
            parsed = output["parsed"]
            result = (
                parsed
                if isinstance(parsed, EvaluationOutput)
                else EvaluationOutput.model_validate(parsed)
            )
        except (TypeError, ValueError):
            raise EvaluationFailed("Resultado del evaluador no válido.") from None
        result.fields = {
            field.key: result.fields.get(
                field.key, FieldValue(value=None, evidence=None, confidence="low")
            )
            for field in request.profile.result_schema
        }
        return result
