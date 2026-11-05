from dataclasses import dataclass, field

from screening.application.capability_gateway import CapabilityGateway
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext


@dataclass
class AgentContext:
    run_context: RunContext
    profile: ProfileContent
    gateway: CapabilityGateway
    flags: set[str] = field(default_factory=set)
    turn_note: str = ""
    tenant_name: str = ""
