from copy import deepcopy

from screening.application.ports import CapabilityCallRecord, LlmUsage


class InMemoryTraceSink:
    def __init__(self) -> None:
        self.llm_calls: list[LlmUsage] = []
        self.capability_calls: list[CapabilityCallRecord] = []

    async def record_llm_call(self, usage: LlmUsage) -> None:
        self.llm_calls.append(deepcopy(usage))

    async def record_capability_call(self, call: CapabilityCallRecord) -> None:
        self.capability_calls.append(deepcopy(call))
