import json
from uuid import uuid4

from screening.domain.capabilities import CATALOG, TOOLS, tools_for
from screening.domain.run_context import RunContext, run_context_from_dict, run_context_to_dict


def test_every_tool_has_a_capability() -> None:
    assert all(tool.capability in CATALOG for tool in TOOLS.values())
    assert all(name in TOOLS for cap in CATALOG.values() for name in cap.tool_names)


def test_tools_are_ordered_and_complete_is_always_available() -> None:
    assert tools_for(frozenset({"openings.find", "interview.book"})) == [
        "openings_find",
        "interview_slots",
        "interview_book",
        "interview_reschedule",
        "interview_cancel",
        "screening_complete",
    ]
    assert tools_for(frozenset()) == ["screening_complete"]


def test_run_context_json_roundtrip() -> None:
    ctx = RunContext(
        agent="conversador",
        tenant_id=uuid4(),
        tenant_slug="grupo_sazon",
        acts_for="tenant",
        talks_to="candidate",
        session_id=uuid4(),
        application_id=uuid4(),
        profile_version_id=None,
        granted=frozenset({"openings.find", "application.read_mine"}),
        autonomy={"qualify": 2},
    )
    data = run_context_to_dict(ctx)
    assert data["granted"] == sorted(ctx.granted)
    assert run_context_from_dict(json.loads(json.dumps(data))) == ctx
