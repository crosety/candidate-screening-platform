import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from web.core.management.commands.load_workspace_example import WorkspaceExample

FIXTURE = Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon/workspace-example.json"


def test_an_interview_belongs_only_to_a_qualified_candidate() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert WorkspaceExample.model_validate(data)
    pool = next(c for c in data["candidates"] if c["verdict"] == "pool")
    pool["interview"] = {"site_id": "VLC-RUZ", "weekday": 3, "time": "17:00"}
    with pytest.raises(ValidationError, match="Solo una candidatura apta"):
        WorkspaceExample.model_validate(data)


@pytest.mark.parametrize("bad", [{"weekday": 7}, {"time": "25:00"}, {"site_id": ""}])
def test_interview_shape_is_validated(bad: dict[str, object]) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    lucia = next(c for c in data["candidates"] if c["key"] == "lucia")
    lucia["interview"] = {**lucia["interview"], **bad}
    with pytest.raises(ValidationError):
        WorkspaceExample.model_validate(data)
