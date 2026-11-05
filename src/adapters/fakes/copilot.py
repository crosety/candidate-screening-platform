from uuid import UUID

from screening.application.ports import CopilotMaterial, Suggestion
from screening.domain.run_context import RunContext


class StaticCopilotMaterialReader:
    def __init__(self, material: CopilotMaterial | None = None) -> None:
        self.material = material or CopilotMaterial([], [], [])

    async def recent(self, tenant_id: UUID) -> CopilotMaterial:
        return self.material


class ScriptedCopilot:
    def __init__(self, suggestions: list[Suggestion] | None = None) -> None:
        self.suggestions = suggestions or []
        self.calls: list[tuple[RunContext, str, str]] = []

    async def suggest(self, ctx: RunContext, section: str, material: str) -> list[Suggestion]:
        self.calls.append((ctx, section, material))
        return self.suggestions
