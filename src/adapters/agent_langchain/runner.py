import asyncio
from typing import Any, Literal, cast

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.middleware.types import InputAgentState
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from adapters.agent_langchain.context import AgentContext
from adapters.agent_langchain.middleware import (
    GuardianMiddleware,
    StrictToolsMiddleware,
    TracingMiddleware,
)
from adapters.agent_langchain.serialization import from_serialized, to_serialized
from adapters.agent_langchain.tools import build_tools
from screening.application.capability_gateway import CapabilityGateway
from screening.application.ports import GuardVerdict, ReplyGuard, TraceSink, TurnRequest, TurnResult
from screening.application.prompts import FIRST_MESSAGE
from screening.domain.capabilities import tools_for
from screening.domain.voice import sentences


class LangChainAgentRunner:
    def __init__(
        self,
        chat_model: BaseChatModel,
        guard: ReplyGuard,
        sink: TraceSink,
        gateway: CapabilityGateway,
        model_id: str,
        effort: str,
        caching_behavior: Literal["ignore", "warn", "raise"] = "warn",
        anthropic_caching: bool = True,
        full_policy_guard: bool = False,
    ) -> None:
        self.chat_model, self.guard, self.sink, self.gateway = chat_model, guard, sink, gateway
        self.model_id, self.effort, self.caching_behavior = model_id, effort, caching_behavior
        self.anthropic_caching = anthropic_caching
        self.full_policy_guard = full_policy_guard

    async def run_turn(self, request: TurnRequest) -> TurnResult:
        history = from_serialized(request.history)
        ctx = request.run_context
        context = AgentContext(
            ctx,
            request.profile,
            self.gateway,
            turn_note=request.turn_note,
            tenant_name=request.tenant_name,
        )
        text = request.text if request.text is not None else FIRST_MESSAGE
        turn = HumanMessage(
            content=[{"type": "text", "text": text}, {"type": "text", "text": request.turn_note}]
        )
        # Built-in middleware extend different state schemas; the list is heterogeneous.
        middleware: list[AgentMiddleware[Any, Any, Any]] = [
            GuardianMiddleware(self.guard, full_policy=self.full_policy_guard),
            TracingMiddleware(self.sink, self.model_id, self.effort),
            StrictToolsMiddleware(),
        ]
        if self.anthropic_caching:
            middleware.append(
                AnthropicPromptCachingMiddleware(
                    ttl="5m", unsupported_model_behavior=self.caching_behavior
                )
            )
        middleware.append(ModelCallLimitMiddleware(run_limit=6, exit_behavior="end"))
        system = SystemMessage(content=request.system_prompt)
        if self.anthropic_caching:
            system = SystemMessage(
                content=[
                    {
                        "type": "text",
                        "text": request.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            )
        agent = create_agent(
            model=self.chat_model,
            tools=build_tools(tools_for(ctx.granted)),
            system_prompt=system,
            context_schema=AgentContext,
            middleware=middleware,
        )
        # Serialization accepts only the four supported LangChain message roles.
        state = await agent.ainvoke(
            cast(InputAgentState, {"messages": [*history, turn]}), context=context
        )
        new = state["messages"][len(history) :]
        final = next(
            (m for m in reversed(new) if isinstance(m, AIMessage) and not m.tool_calls), None
        )
        reply = str(final.text) if final is not None else None
        if reply is not None and not reply.strip():
            context.flags.add("empty_response")
            reply = None
        if reply is not None and reply.startswith("Model call limits exceeded"):
            context.flags.add("limit_reached")
            reply = None
        if request.channel == "voice" and reply and not self.full_policy_guard:
            evidence = GuardianMiddleware(self.guard).evidence_from_messages(
                list(state["messages"]), context
            )
            limit = asyncio.Semaphore(3)

            async def review(sentence: str) -> GuardVerdict:
                async with limit:
                    return await self.guard.review(ctx, sentence, evidence)

            # Independent checks overlap, but no text is released before ALL pass.
            # TaskGroup also cancels sibling calls if a review raises.
            async with asyncio.TaskGroup() as group:
                checks = [group.create_task(review(sentence)) for sentence in sentences(reply)]
            if any(
                not check.result().approved or check.result().reason == "sin veredicto"
                for check in checks
            ):
                context.flags.add("guard_failed")
                reply = None
        return TurnResult(to_serialized(new), reply, frozenset(context.flags))
