from time import perf_counter
from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from adapters.agent_langchain.middleware import record_model_usage
from screening.application.ports import MessageRecord, ResultRecord, TraceSink
from screening.application.prompts import JUDGE_INSTRUCTIONS, build_judge_input
from screening.domain.errors import EvaluationFailed
from screening.domain.evals import JudgeOutput, Persona, normalize_judge
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext


class BedrockJudge:
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

    async def judge(
        self,
        ctx: RunContext,
        profile: ProfileContent,
        persona: Persona,
        transcript: list[MessageRecord],
        result: ResultRecord | None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> JudgeOutput:
        structured = self.structured_model or self.model.with_structured_output(
            JudgeOutput, method="json_schema", include_raw=True
        )
        started = perf_counter()
        output = await structured.ainvoke(
            [
                SystemMessage(JUDGE_INSTRUCTIONS),
                HumanMessage(build_judge_input(profile, persona, transcript, result, evidence)),
            ]
        )
        if not isinstance(output, dict):
            raise EvaluationFailed("El juez no ha devuelto el contenedor esperado.")
        await record_model_usage(
            ctx,
            cast(AIMessage, output["raw"]),
            self.sink,
            self.model_id,
            self.effort,
            "juez",
            round((perf_counter() - started) * 1000),
        )
        if output.get("parsed") is None:
            raise EvaluationFailed("No se ha podido interpretar el resultado del juez.")
        parsed = output["parsed"]
        return normalize_judge(
            parsed if isinstance(parsed, JudgeOutput) else JudgeOutput.model_validate(parsed),
            profile.rubric,
            persona,
        )
