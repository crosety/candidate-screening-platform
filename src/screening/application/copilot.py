import logging
from uuid import UUID

from screening.application.ports import (
    ConversationRepository,
    Copilot,
    CopilotMaterialReader,
    ProfileVersions,
    Suggestion,
)
from screening.application.prompts import build_copilot_material
from screening.domain.errors import NotAllowed
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext

BRIEF_FIELDS = {"role", "who", "fit", "voice", "followup", "avoid"}
logger = logging.getLogger(__name__)


def allowed_brief_fields(profile: ProfileContent) -> set[str]:
    return BRIEF_FIELDS - {"who", "fit"} if profile.schema_version == 3 else BRIEF_FIELDS


class CopilotService:
    def __init__(
        self,
        versions: ProfileVersions,
        tenants: ConversationRepository,
        conversations_reader: CopilotMaterialReader,
        copilot: Copilot,
    ) -> None:
        self.versions, self.tenants = versions, tenants
        self.conversations_reader, self.copilot = conversations_reader, copilot

    async def suggestions(self, tenant_id: UUID, section: str) -> list[Suggestion]:
        if section not in {"brief", "operating_model"}:
            raise NotAllowed("Sección no válida para el copiloto.")
        draft = await self.versions.draft(tenant_id)
        items = await self.conversations_reader.recent(tenant_id)
        tenant = await self.tenants.get_tenant(tenant_id)
        ctx = RunContext(
            agent="copiloto",
            tenant_id=tenant_id,
            tenant_slug=tenant.slug,
            acts_for="tenant",
            talks_to="fde",
            session_id=None,
            application_id=None,
            profile_version_id=None,
            granted=frozenset(),
            autonomy={},
        )
        proposals = await self.copilot.suggest(ctx, section, build_copilot_material(draft, items))
        allowed = allowed_brief_fields(draft.content) if section == "brief" else {"operating_model"}
        accepted: list[Suggestion] = []
        for suggestion in proposals:
            reason = (
                "wrong_section"
                if suggestion.field not in allowed
                else "empty_content"
                if not suggestion.append.strip()
                else "limit"
                if len(accepted) == 3
                else None
            )
            if reason:
                # No candidate evidence, proposed text or tenant identifiers in logs.
                logger.warning("copilot_suggestion_discarded reason=%s", reason)
            else:
                accepted.append(suggestion)
        return accepted
