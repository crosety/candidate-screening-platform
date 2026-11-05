from screening.application.ports import GuardVerdict
from screening.domain.run_context import RunContext


class ScriptedReplyGuard:
    def __init__(self, verdicts: list[GuardVerdict] | None = None) -> None:
        self.verdicts = list(verdicts or [])

    async def review(self, ctx: RunContext, reply: str, sources: str) -> GuardVerdict:
        return self.verdicts.pop(0) if self.verdicts else GuardVerdict(approved=True, reason="ok")
