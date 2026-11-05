import pytest
from pydantic import ValidationError

from screening.domain.profile import ProfileContent


def test_seed_profile_validates(profile: ProfileContent) -> None:
    assert profile.schema_version == 2


def test_v1_is_upgraded_without_mutating_input(profile: ProfileContent) -> None:
    data = profile.model_dump()
    data["schema_version"] = 1
    data.pop("channel_settings")
    for source in data["sources"]:
        source.pop("mode")
    upgraded = ProfileContent.model_validate(data)
    assert upgraded.schema_version == 2
    assert upgraded.channel_settings.model_dump(exclude_none=True) == {}
    assert all(source.mode == "context" for source in upgraded.sources)
    assert data["schema_version"] == 1
    assert "channel_settings" not in data
    assert all("mode" not in source for source in data["sources"])


def test_unknown_schema_version_is_not_silently_upgraded(profile: ProfileContent) -> None:
    with pytest.raises(ValidationError):
        ProfileContent.model_validate({**profile.model_dump(), "schema_version": 3})


def test_channel_settings_only_accept_secret_references(profile: ProfileContent) -> None:
    data = profile.model_dump()
    data["channel_settings"] = {"whatsapp": {"sender_ref": "not-a-secret-reference"}}
    with pytest.raises(ValidationError):
        ProfileContent.model_validate(data)


def test_duplicate_channels_are_rejected(profile: ProfileContent) -> None:
    with pytest.raises(ValidationError, match="repetidos"):
        ProfileContent.model_validate({**profile.model_dump(), "channels": ["web", "web"]})


def test_reject_cannot_be_fully_autonomous(profile: ProfileContent) -> None:
    data = profile.model_dump()
    data["autonomy"]["reject"] = 3
    with pytest.raises(ValidationError):
        ProfileContent.model_validate(data)


def test_duplicate_connection_ids(profile: ProfileContent) -> None:
    data = profile.model_dump()
    data["connections"].append(data["connections"][0])
    with pytest.raises(ValidationError, match="repetidos"):
        ProfileContent.model_validate(data)


def test_unknown_field(profile: ProfileContent) -> None:
    with pytest.raises(ValidationError):
        ProfileContent.model_validate({**profile.model_dump(), "unknown": True})
