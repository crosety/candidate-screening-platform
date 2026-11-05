from uuid import UUID

from screening.application.ports import (
    ConnectorRegistry,
    ConversationRepository,
    DecisionRepository,
    ProfileVersions,
)


class OutboxService:
    def __init__(
        self,
        decisions: DecisionRepository,
        conversations: ConversationRepository,
        versions: ProfileVersions,
        registry: ConnectorRegistry,
    ) -> None:
        self.decisions, self.conversations = decisions, conversations
        self.versions, self.registry = versions, registry

    async def dispatch(self, tenant_id: UUID, item_id: UUID) -> None:
        item = await self.decisions.get_outbox(tenant_id, item_id)
        if item.status in ("sent", "dead"):
            return
        session = await self.conversations.get_session(tenant_id, item.session_id)
        profile = (await self.versions.get(tenant_id, session.profile_version_id)).content
        ats = next(
            (c for c in profile.connections if c.kind == "ats" and c.permission == "write"),
            None,
        )
        if ats is None:
            await self.decisions.mark_outbox(tenant_id, item_id, "dead", "Sin ATS con escritura")
            return
        try:
            tenant = await self.conversations.get_tenant(tenant_id)
            connector = self.registry.get(tenant.slug, ats)
            operation = {"ats.write_result": "write_result", "ats.set_stage": "set_stage"}[
                item.kind
            ]
            response = await connector.call(operation, item.payload)
            if response.get("status") != "ok":
                raise ValueError("El ATS no ha confirmado la escritura")
            await self.decisions.mark_outbox(tenant_id, item_id, "sent")
        except Exception as exc:
            await self.decisions.mark_outbox(tenant_id, item_id, "pending", str(exc))
            raise
