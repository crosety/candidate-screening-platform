import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from screening.application.channel_context import (
    asks_for_call,
    call_request_language,
    channel_note,
)
from screening.application.ports import (
    AgentRunner,
    ApplicationRecord,
    Clock,
    ConversationRepository,
    MessageRecord,
    NewApplication,
    ProfileVersions,
    SessionRecord,
    SignalClassifier,
    TurnRequest,
)
from screening.application.prompts import (
    build_system_prompt,
    build_turn_note,
    call_request_text,
    current_conversation_prompt,
    escape_candidate_text,
    handoff_text,
)
from screening.application.turn_lock import InProcessTurnLock, TurnLock
from screening.domain.errors import Conflict, NotAllowed
from screening.domain.profile import ProfileContent, profile_for_offer
from screening.domain.run_context import RunContext
from screening.domain.signals import TurnSignal


@dataclass(frozen=True)
class OpenedSession:
    session_id: UUID
    tenant_name: str
    status: str
    offer_title: str = ""
    is_test: bool = False


@dataclass(frozen=True)
class TurnOutcome:
    message: MessageRecord | None
    status: str
    action: str = ""


class ConversationService:
    MAX_INCOMING = 40
    SIGNAL_TIMEOUT_SECONDS = 8.0

    def __init__(
        self,
        repo: ConversationRepository,
        versions: ProfileVersions,
        runner: AgentRunner,
        clock: Clock,
        chat_base_url: str,
        turn_lock: TurnLock | None = None,
        channel_context: Callable[[SessionRecord, str], Awaitable[str]] | None = None,
        signals: SignalClassifier | None = None,
    ) -> None:
        self.repo, self.versions, self.runner, self.clock = repo, versions, runner, clock
        self.chat_base_url = chat_base_url
        self.turn_lock = turn_lock or InProcessTurnLock()
        self.channel_context = channel_context
        self.signals = signals

    async def ingest(self, tenant_slug: str, data: NewApplication) -> tuple[ApplicationRecord, str]:
        tenant = await self.repo.get_tenant_by_slug(tenant_slug)
        application = await self.repo.upsert_application(tenant.id, data)
        token = await self.repo.get_or_create_link(tenant.id, application.id)
        return application, f"{self.chat_base_url}/c/{token}"

    async def open_session(
        self, token: str, channel: str = "web", eval_case_id: UUID | None = None
    ) -> OpenedSession:
        link = await self.repo.resolve_link(token)
        tenant = await self.repo.get_tenant(link.tenant_id)
        latest = await self.repo.latest_session(link.tenant_id, link.application_id)
        application = await self.repo.get_application(tenant.id, link.application_id)
        if latest is not None:
            stored = await self.versions.get(tenant.id, latest.profile_version_id)
            offer = next(
                (o for o in stored.content.offers if o.key == latest.run_context.offer_key), None
            )
            return OpenedSession(
                latest.id,
                tenant.name,
                latest.status,
                offer.title if offer else application.job_ref,
                application.is_test,
            )
        session_id = uuid4()
        version = await self.versions.for_new_session(tenant.id, session_id, application.segment)
        profile = ProfileContent.model_validate(version.content)
        offer = next((o for o in profile.offers if o.key == application.job_ref), None)
        if offer is not None and offer.status != "open":
            raise NotAllowed("Esta oferta no está abierta a nuevas conversaciones.")
        if profile.offers and offer is None:
            raise NotAllowed("La candidatura necesita una oferta publicada válida.")
        offer_key = offer.key if offer else ""
        profile = profile_for_offer(profile, offer_key)
        ctx = RunContext(
            agent="conversador",
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            acts_for="tenant",
            talks_to="candidate",
            session_id=session_id,
            application_id=link.application_id,
            profile_version_id=version.id,
            granted=frozenset(profile.grants.conversador),
            autonomy=profile.autonomy.model_dump(),
            eval_case_id=eval_case_id,
            offer_key=offer_key,
        )
        row = await self.repo.create_session(
            tenant.id,
            session_id,
            link.application_id,
            version.id,
            channel,
            ctx,
            build_system_prompt(profile, tenant.name, channel),
        )
        if row.id == session_id:
            await self.repo.add_event(
                tenant.id, session_id, "session_opened", {"version": version.version}
            )
        return OpenedSession(
            row.id,
            tenant.name,
            row.status,
            offer.title if offer else application.job_ref,
            application.is_test,
        )

    async def authorize(self, token: str, session_id: UUID) -> SessionRecord:
        link = await self.repo.resolve_link(token)
        session = await self.repo.get_session(link.tenant_id, session_id)
        if session.application_id != link.application_id:
            raise NotAllowed("Enlace no válido para esta conversación.")
        return session

    async def start(self, tenant_id: UUID, session_id: UUID, channel: str = "web") -> TurnOutcome:
        async with self.turn_lock.hold(tenant_id, session_id):
            session = await self.repo.get_session(tenant_id, session_id)
            if (
                session.status != "active"
                or await self.repo.list_messages(tenant_id, session_id)
                or await self.repo.load_history(tenant_id, session_id)
            ):
                return TurnOutcome(None, session.status)
            # A language chosen before the first message (e.g. WhatsApp intake) shapes the greeting.
            known = TurnSignal(language=session.language) if session.language else None
            return await self._turn(session, None, channel, signal=known)

    async def choose_language(
        self, token: str, session_id: UUID, language: str = "", fallback: str = ""
    ) -> SessionRecord:
        if language not in {"", "es", "en"} or fallback not in {"", "es", "en"}:
            raise NotAllowed("Idioma no admitido.")
        authorized = await self.authorize(token, session_id)
        async with self.turn_lock.hold(authorized.tenant_id, session_id):
            session = await self.authorize(token, session_id)
            preferred = language or session.preferred_language
            fallback = session.fallback_language or fallback
            if (preferred, fallback) != (session.preferred_language, session.fallback_language):
                await self.repo.set_language_choice(
                    session.tenant_id, session.id, preferred, fallback
                )
                await self.repo.add_event(
                    session.tenant_id,
                    session.id,
                    "language_selected",
                    {"preferred": preferred, "fallback": fallback},
                )
                session = await self.repo.get_session(session.tenant_id, session.id)
            return session

    async def handle_message(
        self,
        tenant_id: UUID,
        session_id: UUID,
        client_message_id: str,
        text: str,
        *,
        channel: str = "web",
        provider_message_id: str = "",
        simulated: bool = False,
    ) -> TurnOutcome:
        async with self.turn_lock.hold(tenant_id, session_id):
            session = await self.repo.get_session(tenant_id, session_id)
            if session.status == "handed_off":
                incoming = await self.repo.add_message(
                    tenant_id,
                    session_id,
                    "in",
                    text,
                    client_message_id,
                    channel=channel,
                    provider_message_id=provider_message_id,
                    simulated=simulated,
                )
                return TurnOutcome(incoming, "handed_off")
            if session.status != "active":
                raise NotAllowed("session_closed")
            incoming = await self.repo.add_message(
                tenant_id,
                session_id,
                "in",
                text,
                client_message_id,
                channel=channel,
                provider_message_id=provider_message_id,
                simulated=simulated,
            )
            if incoming is None:
                return TurnOutcome(None, session.status)
            if channel == "web" and asks_for_call(text):
                # A channel request is not an interview answer or authority to dial.
                await self.repo.add_event(tenant_id, session_id, "telephone_form_requested", {})
                reply = await self.repo.add_message(
                    tenant_id,
                    session_id,
                    "out",
                    call_request_text(call_request_language(text)),
                    channel=channel,
                    simulated=simulated,
                )
                return TurnOutcome(reply, session.status, "telephone_request")
            if await self.repo.count_incoming(tenant_id, session_id) > self.MAX_INCOMING:
                await self.repo.set_session_status(tenant_id, session_id, "handed_off")
                await self.repo.add_event(
                    tenant_id, session_id, "auto_handoff", {"reason": "too_many_messages"}
                )
                return TurnOutcome(
                    await self.repo.add_message(
                        tenant_id,
                        session_id,
                        "out",
                        handoff_text(session.response_language),
                        channel=channel,
                        simulated=simulated,
                    ),
                    "handed_off",
                )
            if text.strip().lower() in {"persona", "human", "person"}:
                await self.repo.set_session_status(tenant_id, session_id, "handed_off")
                await self.repo.add_event(
                    tenant_id, session_id, "handoff_requested", {"reason": "keyword"}
                )
                return TurnOutcome(
                    await self.repo.add_message(
                        tenant_id,
                        session_id,
                        "out",
                        (
                            "De acuerdo, te paso con una persona del equipo. "
                            "Se pondrá en contacto contigo."
                            if text.strip().lower() == "persona"
                            else "I'll connect you with a member of the team. "
                            "They will contact you."
                        ),
                        channel=channel,
                        simulated=simulated,
                    ),
                    "handed_off",
                )
            signal = await self._observe(session, incoming.id, text)
            return await self._turn(
                session, escape_candidate_text(text), channel, simulated, signal
            )

    async def stop(self, tenant_id: UUID, session_id: UUID) -> None:
        async with self.turn_lock.hold(tenant_id, session_id):
            current = await self.repo.get_session(tenant_id, session_id)
            if current.status == "active":
                await self.repo.set_session_status(tenant_id, session_id, "abandoned")
            await self.repo.add_event(tenant_id, session_id, "channel_opt_out", {})

    async def _handoff_text(self, tenant_id: UUID, session_id: UUID) -> str:
        """The platform hand-off notice, in the language observed so far in this turn."""
        session = await self.repo.get_session(tenant_id, session_id)
        return handoff_text(session.response_language)

    async def _observe(self, session: SessionRecord, message_id: UUID, text: str) -> TurnSignal:
        """Language and mood of this message; an observation for tone, never for the verdict."""
        if self.signals is None:
            return TurnSignal()
        try:
            signal = await asyncio.wait_for(
                self.signals.classify(session.run_context, text), self.SIGNAL_TIMEOUT_SECONDS
            )
        except Exception as exc:
            await self.repo.add_event(
                session.tenant_id,
                session.id,
                "turn_signal_failed",
                {"error_type": type(exc).__name__},
            )
            return TurnSignal()
        signal = signal.grounded(text)
        await self.repo.record_signal(session.tenant_id, session.id, message_id, signal)
        await self.repo.add_event(
            session.tenant_id,
            session.id,
            "turn_signal",
            {
                "message_id": str(message_id),
                "language": signal.language,
                "code_switching": signal.code_switching,
                "mood": signal.mood,
                "tone": signal.tone,
            },
        )
        return signal

    async def _turn(
        self,
        session: SessionRecord,
        text: str | None,
        channel: str = "web",
        simulated: bool = False,
        signal: TurnSignal | None = None,
    ) -> TurnOutcome:
        tenant_id, session_id = session.tenant_id, session.id
        history = await self.repo.load_history(tenant_id, session_id)
        version = await self.versions.get(tenant_id, session.profile_version_id)
        application = await self.repo.get_application(tenant_id, session.application_id)
        context = (
            await self.channel_context(session, channel)
            if self.channel_context
            else channel_note(channel)
        )
        try:
            result = await self.runner.run_turn(
                TurnRequest(
                    session.run_context,
                    profile_for_offer(version.content, session.run_context.offer_key),
                    current_conversation_prompt(session.system_prompt) + context,
                    history,
                    text,
                    "\n".join(
                        part
                        for part in (
                            build_turn_note(self.clock.now(), application.timezone, channel),
                            signal.note() if signal else "",
                            self._language_note(
                                replace(session, language=signal.language or session.language)
                                if signal
                                else session
                            ),
                        )
                        if part
                    ),
                    channel,
                    (await self.repo.get_tenant(tenant_id)).name,
                )
            )
        except Exception as exc:
            # Keep the incoming message, record only the exception class, and fail safely.
            await self.repo.add_event(
                tenant_id,
                session_id,
                "provider_failed",
                {"error_type": type(exc).__name__, "channel": channel},
            )
            await self.repo.set_session_status(tenant_id, session_id, "handed_off")
            message = await self.repo.add_message(
                tenant_id,
                session_id,
                "out",
                await self._handoff_text(tenant_id, session_id),
                channel=channel,
                simulated=simulated,
            )
            return TurnOutcome(message, "handed_off")
        try:
            await self.repo.append_history(tenant_id, session_id, len(history), result.new_messages)
        except Conflict:
            current = await self.repo.get_session(tenant_id, session_id)
            return TurnOutcome(None, current.status)
        if result.reply is None or result.flags:
            reply = await self._handoff_text(tenant_id, session_id)
            await self.repo.set_session_status(tenant_id, session_id, "handed_off")
            await self.repo.add_event(
                tenant_id, session_id, "auto_handoff", {"flags": sorted(result.flags)}
            )
        else:
            reply = result.reply
        message = await self.repo.add_message(
            tenant_id, session_id, "out", reply, channel=channel, simulated=simulated
        )
        current = await self.repo.get_session(tenant_id, session_id)
        return TurnOutcome(message, current.status)

    @staticmethod
    def _language_note(session: SessionRecord) -> str:
        language = session.response_language
        if not language:
            return ""
        source = "elegido en el selector" if session.preferred_language else "de continuidad"
        return (
            f"[plataforma: idioma {source}: {language}] "
            "Usa ese idioma desde el saludo y en las siguientes respuestas, aunque un nombre "
            "o una cifra no permita detectar idioma. Respeta los idiomas admitidos por el brief "
            "y una petición posterior explícita del candidato. No reescribas mensajes anteriores."
        )

    async def compose_followup(
        self,
        session: SessionRecord,
        reason: str,
        channel: str = "whatsapp",
        at: datetime | None = None,
    ) -> str:
        """A scheduled platform turn; it must not impersonate a candidate or take actions."""
        version = await self.versions.get(session.tenant_id, session.profile_version_id)
        app = await self.repo.get_application(session.tenant_id, session.application_id)
        history = await self.repo.load_history(session.tenant_id, session.id)
        ctx = replace(session.run_context, granted=frozenset(), autonomy={})
        language = self._language_note(session)
        result = await self.runner.run_turn(
            TurnRequest(
                ctx,
                profile_for_offer(version.content, session.run_context.offer_key),
                current_conversation_prompt(session.system_prompt),
                history,
                "[plataforma] Turno programado de reenganche; el candidato no ha escrito.",
                build_turn_note(at or self.clock.now(), app.timezone, channel)
                + "\nLa plataforma ya ha comprobado el horario permitido, el número de intentos y "
                "el consentimiento: este recordatorio se envía ahora. Escribe solo el mensaje "
                "para el candidato, breve y respetuoso, que retome el punto pendiente sin "
                "presionar. Nunca escribas notas internas ni decidas si enviarlo. "
                + language
                + "No tomes decisiones ni solicites herramientas. Motivo registrado: "
                + reason,
                channel,
            )
        )
        if result.flags or not result.reply:
            raise NotAllowed("El recordatorio necesita revisión humana.")
        await self.repo.append_history(
            session.tenant_id, session.id, len(history), result.new_messages
        )
        return result.reply

    async def remind(
        self,
        session: SessionRecord,
        reason: str,
        channel: str,
        *,
        simulated: bool = False,
        at: datetime | None = None,
    ) -> MessageRecord:
        """Leave the reminder in the candidate's own conversation (web chat or simulation)."""
        text = await self.compose_followup(session, reason, channel, at)
        message = await self.repo.add_message(
            session.tenant_id, session.id, "out", text, channel=channel, simulated=simulated
        )
        assert message is not None
        await self.repo.add_event(
            session.tenant_id,
            session.id,
            "reengagement_sent",
            {"channel": channel, "reason": reason, "message_id": str(message.id)},
        )
        return message
