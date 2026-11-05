import pytest

from screening.domain.diff import diff_profiles
from screening.domain.profile import ProfileContent, ResultField, WhatsAppChannelSettings


def test_no_changes(profile: ProfileContent) -> None:
    diff = diff_profiles(profile, profile.model_copy(deep=True), True)
    assert diff.risk == "none"
    assert diff.changes == []


def test_brief_added_sentence(profile: ProfileContent) -> None:
    after = profile.model_copy(deep=True)
    after.brief.fit += " Nueva frase."
    diff = diff_profiles(profile, after, True)
    assert diff.risk == "medium"
    assert diff.changes[0].sentences is not None
    assert ("+", "Nueva frase.") in diff.changes[0].sentences


def test_raise_autonomy(profile: ProfileContent) -> None:
    after = profile.model_copy(deep=True)
    after.autonomy.reject = 2
    diff = diff_profiles(profile, after, True)
    assert diff.risk == "high"
    assert diff.needs_second_approval


@pytest.mark.parametrize("client_required", [True, False])
def test_result_contract_change(profile: ProfileContent, client_required: bool) -> None:
    after = profile.model_copy(deep=True)
    after.result_schema.append(ResultField(key="nuevo", description="Dato", ats_field="custom.new"))
    diff = diff_profiles(profile, after, client_required)
    assert diff.needs_client_approval is client_required


def test_remove_capability(profile: ProfileContent) -> None:
    after = profile.model_copy(deep=True)
    after.grants.conversador.remove("application.read_mine")
    assert diff_profiles(profile, after, True).risk == "low"


def test_first_version(profile: ProfileContent) -> None:
    diff = diff_profiles(None, profile, True)
    assert any(c.label == "Primera versión" for c in diff.changes)
    assert diff.needs_client_approval


def test_channel_provider_changes_require_approvals(profile: ProfileContent) -> None:
    after = profile.model_copy(deep=True)
    after.channel_settings.whatsapp = WhatsAppChannelSettings(sender_ref="secret://demo/whatsapp")
    diff = diff_profiles(profile, after, True)
    assert diff.risk == "high"
    assert diff.needs_second_approval and diff.needs_client_approval
