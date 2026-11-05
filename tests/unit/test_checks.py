import pytest

from screening.domain.checks import run_checks
from screening.domain.profile import ProfileContent


def test_seed_has_no_errors(profile: ProfileContent) -> None:
    assert not [c for c in run_checks(profile) if c.severity == "error"]


@pytest.mark.parametrize(
    "code",
    [
        "E001",
        "E002",
        "E003",
        "E004",
        "E005",
        "E006",
        "E007",
        "W001",
        "W002",
        "W003",
        "W004",
        "W005",
        "I001",
    ],
)
def test_check_codes(profile: ProfileContent, code: str) -> None:
    if code == "E001":
        profile.result_schema[0].ats_field = ""
    elif code == "E002":
        profile.connections = []
    elif code == "E003":
        profile.connections[0].secret_ref = "not-a-reference"
    elif code == "E004":
        profile.connections = [c for c in profile.connections if c.kind != "ats"]
    elif code == "E005":
        profile.connections[0].permission = "read"
    elif code == "E006":
        profile.connections[1].backs = []
    elif code == "E007":
        profile.grants.conversador.remove("interview.book")
    elif code == "W001":
        profile.brief.voice = ""
    elif code == "W002":
        profile.brief.voice = "[Completa el tono]"
    elif code == "W003":
        profile.operating_model = ""
    elif code == "W004":
        profile.brief.who = "Exige un certificado."
    checks = run_checks(profile, ["Aviso vivo"] if code == "W005" else None)
    assert code in {c.code for c in checks}
    order = {"error": 0, "warning": 1, "info": 2}
    assert [(order[c.severity], c.code) for c in checks] == sorted(
        (order[c.severity], c.code) for c in checks
    )
