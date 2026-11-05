from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    tool_names: tuple[str, ...]
    needs_kinds: tuple[str, ...]
    helper_kinds: tuple[str, ...]
    writes: bool
    dial_decision: str | None
    agents: tuple[str, ...]


CATALOG: dict[str, CapabilitySpec] = {
    spec.id: spec
    for spec in (
        CapabilitySpec("faq.search", ("faq_search",), (), (), False, None, ("conversador",)),
        CapabilitySpec(
            "openings.find", ("openings_find",), ("data",), ("geo",), False, None, ("conversador",)
        ),
        CapabilitySpec(
            "application.read_mine",
            ("application_read_mine",),
            ("ats",),
            (),
            False,
            None,
            ("conversador",),
        ),
        CapabilitySpec(
            "interview.book",
            ("interview_slots", "interview_book", "interview_reschedule", "interview_cancel"),
            ("calendar",),
            (),
            True,
            "schedule",
            ("conversador",),
        ),
        CapabilitySpec(
            "handoff.request", ("handoff_request",), (), (), True, "handoff", ("conversador",)
        ),
        CapabilitySpec(
            "credential.verify",
            ("credential_verify",),
            ("registry",),
            (),
            False,
            None,
            ("conversador",),
        ),
        CapabilitySpec(
            "followup.schedule", ("followup_schedule",), (), (), True, "followup", ("conversador",)
        ),
        CapabilitySpec(
            "screening.complete", ("screening_complete",), (), (), True, None, ("conversador",)
        ),
        CapabilitySpec("candidates.query", (), (), (), False, None, ("recruiter",)),
        CapabilitySpec("analytics.aggregates", (), (), (), False, None, ("recruiter",)),
    )
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    capability: str
    operation: str | None
    writes: bool


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec("faq_search", "faq.search", None, False),
        ToolSpec("openings_find", "openings.find", "find_openings", False),
        ToolSpec("application_read_mine", "application.read_mine", "get_application", False),
        ToolSpec("interview_slots", "interview.book", "list_slots", False),
        ToolSpec("interview_book", "interview.book", "book_slot", True),
        ToolSpec("interview_reschedule", "interview.book", "reschedule_booking", True),
        ToolSpec("interview_cancel", "interview.book", "cancel_booking", True),
        ToolSpec("handoff_request", "handoff.request", None, True),
        ToolSpec("credential_verify", "credential.verify", "verify_credential", False),
        ToolSpec("followup_schedule", "followup.schedule", None, True),
        ToolSpec("screening_complete", "screening.complete", None, True),
    )
}


def tools_for(granted: frozenset[str]) -> list[str]:
    return [
        name
        for name, tool in TOOLS.items()
        if tool.capability in granted or tool.capability == "screening.complete"
    ]
