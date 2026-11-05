import functools
from dataclasses import dataclass

from adapters.agent_langchain.copilot import BedrockCopilot
from adapters.agent_langchain.evaluator import BedrockEvaluator
from adapters.agent_langchain.guard import BedrockReplyGuard
from adapters.agent_langchain.jev_guard import ESCALATION_PROMPT, JevReplyGuard
from adapters.agent_langchain.judge import BedrockJudge
from adapters.agent_langchain.recruiter import ReadOnlyRecruiter
from adapters.agent_langchain.runner import LangChainAgentRunner
from adapters.agent_langchain.signals import ModelSignalClassifier
from adapters.agent_langchain.simulator import BedrockCandidateSimulator
from adapters.channels.web_chat import WebChatNotifier
from adapters.connectors_registry import DefaultConnectorRegistry
from adapters.eval_harness import harness_metadata
from adapters.fakes.agent import EchoAgentRunner
from adapters.fakes.channel import SimulatedWhatsAppAdapter
from adapters.fakes.copilot import ScriptedCopilot
from adapters.fakes.evals import PerfectJudge, ScriptedSimulator
from adapters.fakes.evaluator import ScriptedEvaluator
from adapters.fakes.signals import KeywordSignalClassifier
from adapters.llm.bedrock import MODEL_IDS, ROLE_CONFIG, build_chat_model
from adapters.llm.embeddings import FakeEmbeddings, OpenAIEmbeddings
from adapters.llm.openai import CONVERSATION_MODEL_ID, MODEL_ID, ROLE_EFFORT, build_openai_model
from adapters.persistence.channels import DjangoChannelStore
from adapters.persistence.conversations import DjangoConversationRepository
from adapters.persistence.copilot import DjangoCopilotMaterialReader
from adapters.persistence.decisions import DjangoDecisionRepository
from adapters.persistence.evals import DjangoEvalRepository
from adapters.persistence.evals_gate import django_evals_gate
from adapters.persistence.faq import PgvectorFaqStore, prepare_faq
from adapters.persistence.followups import DjangoFollowupStore
from adapters.persistence.interviews import DjangoInterviewStore
from adapters.persistence.jobs import DjangoJobQueue
from adapters.persistence.live import DjangoConnectorStatusRepository, django_live_warnings
from adapters.persistence.profile_versions import AsyncProfileVersions
from adapters.persistence.profiles import DjangoProfileRepository
from adapters.persistence.reengagement import DjangoSilenceStore
from adapters.persistence.tracing import DjangoTraceSink
from adapters.persistence.turn_lock import PostgresTurnLock
from adapters.personas import FilePersonaSource
from adapters.secrets.env import EnvSecretStore
from adapters.system import SystemClock
from screening.application.assessment import AssessmentService, DecisionBookingPolicy
from screening.application.capability_gateway import CapabilityGateway
from screening.application.channels import ChannelService
from screening.application.conversation import ConversationService
from screening.application.copilot import CopilotService
from screening.application.evals import EvalService
from screening.application.faq import FaqService
from screening.application.followups import FollowupService
from screening.application.inbox import InboxService
from screening.application.interviews import InterviewService
from screening.application.live import LiveService
from screening.application.outbox import OutboxService
from screening.application.ports import (
    AgentRunner,
    CandidateSimulator,
    Clock,
    Copilot,
    DecisionRepository,
    Evaluator,
    Judge,
    MetricsDelivery,
    ReplyGuard,
    SecretStore,
    SignalClassifier,
    TraceSink,
)
from screening.application.profiles import ProfileService
from screening.application.recruiter import RecruiterAssistant
from screening.application.reengagement import ReengagementService
from screening.domain.result import EvaluationOutput
from screening.domain.run_context import RunContext
from screening.platform.chat_push import push_to_chat


def build_metrics_delivery() -> MetricsDelivery:
    from django.conf import settings

    from adapters.llm.langfuse import LangfuseConfig, LangfuseExporter

    return LangfuseExporter(
        LangfuseConfig.load(
            settings.LANGFUSE_CONFIG_FILE, settings.LANGFUSE_PROJECT_ID, settings.LANGFUSE_HOST
        ),
        voice_key_file=settings.ELEVENLABS_API_KEY_FILE,
        environment=settings.LANGFUSE_ENVIRONMENT,
    )


@dataclass(frozen=True)
class Container:
    clock: Clock
    secrets: SecretStore
    profiles: ProfileService
    conversations: ConversationService
    gateway: CapabilityGateway
    sink: TraceSink
    decisions: DecisionRepository
    assessment: AssessmentService
    outbox: OutboxService
    inbox: InboxService
    evals: EvalService
    live: LiveService
    copilot: CopilotService
    channels: ChannelService
    followups: FollowupService
    reengagement: ReengagementService
    recruiter: RecruiterAssistant
    faq: FaqService
    interviews: InterviewService


def build_container(llm_mode: str) -> Container:
    from django.conf import settings
    from django.db import connection
    from psycopg.conninfo import make_conninfo

    if llm_mode not in ("fake", "bedrock", "replay", "openai"):
        raise ValueError("LLM_MODE no válido")
    clock = SystemClock()
    profiles = ProfileService(
        DjangoProfileRepository(),
        clock,
        evals_gate=django_evals_gate if settings.PUBLISH_REQUIRES_EVALS else None,
        live_warnings=django_live_warnings,
    )
    repo = DjangoConversationRepository()
    sink = DjangoTraceSink()
    decisions, queue = DjangoDecisionRepository(), DjangoJobQueue()
    versions, registry = AsyncProfileVersions(profiles), DefaultConnectorRegistry(clock)
    evaluator: Evaluator
    if llm_mode == "fake":
        evaluator = ScriptedEvaluator(
            [
                EvaluationOutput(
                    verdict="review",
                    fields={},
                    flags=[],
                    reason="Modo de prueba",
                    summary="Modo de prueba: sin evaluación real.",
                )
            ]
        )
        evaluator_model = "fake"
    elif llm_mode == "openai":
        evaluator_model = MODEL_ID
        evaluator = BedrockEvaluator(
            build_openai_model("evaluador"), sink, MODEL_ID, ROLE_EFFORT["evaluador"]
        )
    else:
        evaluator_model = MODEL_IDS["evaluador"]
        evaluator = BedrockEvaluator(
            build_chat_model("evaluador"), sink, evaluator_model, ROLE_CONFIG["evaluador"][0]
        )
    assessment = AssessmentService(
        decisions, repo, versions, evaluator, queue, clock, evaluator_model
    )
    gateway = CapabilityGateway(
        repo, registry, sink, clock, DecisionBookingPolicy(decisions, repo, queue), assessment
    )
    simulator: CandidateSimulator
    judge: Judge
    if llm_mode == "fake":
        simulator, judge = ScriptedSimulator([]), PerfectJudge()
    elif llm_mode == "openai":
        simulator = BedrockCandidateSimulator(
            build_openai_model("simulador"), sink, MODEL_ID, ROLE_EFFORT["simulador"]
        )
        judge = BedrockJudge(build_openai_model("juez"), sink, MODEL_ID, ROLE_EFFORT["juez"])
    else:
        simulator = BedrockCandidateSimulator(
            build_chat_model("simulador"), sink, MODEL_IDS["simulador"], ROLE_CONFIG["simulador"][0]
        )
        judge = BedrockJudge(
            build_chat_model("juez"), sink, MODEL_IDS["juez"], ROLE_CONFIG["juez"][0]
        )

    def runner_factory(gateway: CapabilityGateway) -> AgentRunner:
        if llm_mode == "fake":
            return EchoAgentRunner()
        if llm_mode == "openai":
            use_jev = settings.JEV_MODE != "off"
            chat_model = build_openai_model("conversador")
            fallback = BedrockReplyGuard(
                build_openai_model("guardian"),
                sink,
                MODEL_ID,
                ROLE_EFFORT["guardian"],
                **({"prompt": ESCALATION_PROMPT} if use_jev else {}),
            )
            guard: ReplyGuard = fallback
            if use_jev:

                async def is_synthetic(ctx: RunContext) -> bool:
                    if ctx.eval_case_id is not None:
                        return True
                    if ctx.application_id is None:
                        return False
                    return (await repo.get_application(ctx.tenant_id, ctx.application_id)).is_test

                guard = JevReplyGuard(
                    settings.AI_GATEWAY_API_KEY_FILE,
                    fallback,
                    sink,
                    synthetic_only=settings.JEV_MODE == "synthetic",
                    is_synthetic=is_synthetic,
                )
            return LangChainAgentRunner(
                chat_model,
                guard,
                sink,
                gateway,
                CONVERSATION_MODEL_ID,
                ROLE_EFFORT["conversador"],
                anthropic_caching=False,
                full_policy_guard=use_jev,
            )
        return LangChainAgentRunner(
            build_chat_model("conversador"),
            BedrockReplyGuard(
                build_chat_model("guardian"),
                sink,
                MODEL_IDS["guardian"],
                ROLE_CONFIG["guardian"][0],
            ),
            sink,
            gateway,
            MODEL_IDS["conversador"],
            ROLE_CONFIG["conversador"][0],
        )

    copilot: Copilot
    if llm_mode == "fake":
        copilot = ScriptedCopilot()
    elif llm_mode == "openai":
        copilot = BedrockCopilot(
            build_openai_model("copiloto"), sink, MODEL_ID, ROLE_EFFORT["copiloto"]
        )
    else:
        copilot = BedrockCopilot(
            build_chat_model("copiloto"), sink, MODEL_IDS["copiloto"], ROLE_CONFIG["copiloto"][0]
        )
    db = connection.settings_dict
    turn_lock = PostgresTurnLock(
        make_conninfo(
            dbname=db["NAME"],
            user=db["USER"],
            password=db["PASSWORD"],
            host=db["HOST"],
            port=db["PORT"],
        )
    )
    from screening.platform.channel_context import context_for

    # Only the active provider classifies with a model; legacy Bedrock/replay runs keep the
    # deterministic double so recorded cassettes stay valid.
    signals: SignalClassifier = (
        ModelSignalClassifier(build_openai_model("senales"), sink, MODEL_ID, ROLE_EFFORT["senales"])
        if llm_mode == "openai"
        else KeywordSignalClassifier()
    )
    conversations = ConversationService(
        repo,
        versions,
        runner_factory(gateway),
        clock,
        settings.CHAT_BASE_URL,
        turn_lock,
        channel_context=context_for,
        signals=signals,
    )
    channel_store = DjangoChannelStore()
    channels = ChannelService(
        conversations, channel_store, SimulatedWhatsAppAdapter(channel_store, clock), clock
    )
    followups = FollowupService(channels, DjangoFollowupStore(), web_push=push_to_chat)
    gateway.followups = followups
    faq = FaqService(
        OpenAIEmbeddings(sink) if llm_mode == "openai" else FakeEmbeddings(), PgvectorFaqStore()
    )
    gateway.faq = faq
    interviews = InterviewService(
        DjangoInterviewStore(),
        repo,
        versions,
        registry,
        decisions,
        queue,
        clock,
        WebChatNotifier(repo, push_to_chat),
        organizer_email=getattr(settings, "INTERVIEW_ORGANIZER_EMAIL", "no-reply@orbio.invalid"),
    )
    gateway.interviews = interviews
    profiles.prepare_version = functools.partial(prepare_faq, faq)
    return Container(
        faq=faq,
        interviews=interviews,
        recruiter=ReadOnlyRecruiter(
            build_openai_model("copiloto")
            if llm_mode == "openai"
            else build_chat_model("copiloto")
            if llm_mode != "fake"
            else None,
            sink,
            MODEL_ID if llm_mode == "openai" else MODEL_IDS["copiloto"],
            ROLE_EFFORT["copiloto"] if llm_mode == "openai" else ROLE_CONFIG["copiloto"][0],
        ),
        clock=clock,
        secrets=EnvSecretStore(),
        profiles=profiles,
        conversations=conversations,
        channels=channels,
        followups=followups,
        reengagement=ReengagementService(DjangoSilenceStore(), conversations, clock),
        gateway=gateway,
        sink=sink,
        decisions=decisions,
        assessment=assessment,
        outbox=OutboxService(decisions, repo, versions, registry),
        inbox=InboxService(decisions, repo, versions, queue, clock),
        live=LiveService(versions, DjangoConnectorStatusRepository(), registry, repo, clock),
        copilot=CopilotService(versions, repo, DjangoCopilotMaterialReader(), copilot),
        evals=EvalService(
            DjangoEvalRepository(),
            versions,
            FilePersonaSource(settings.SEEDS_DIR),
            repo,
            registry,
            runner_factory,
            evaluator,
            simulator,
            judge,
            sink,
            clock,
            queue,
            evaluator_model,
            harness=harness_metadata(llm_mode),
            faq=faq,
            signals=signals,
        ),
    )


@functools.cache
def get_container() -> Container:
    from django.conf import settings

    return build_container(settings.LLM_MODE)
