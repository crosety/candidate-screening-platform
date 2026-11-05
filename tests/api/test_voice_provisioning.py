import json

import httpx
import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.core.management.base import CommandError

from screening.platform.container import get_container
from screening.platform.voice_runtime import DIRECT_MODEL, agent_registry_key, policy_hash
from tests.api.test_telephone import phone, setup

__all__ = ["phone", "setup"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("recording", [False, True, None])
def test_provisioning_requires_confirmed_audio_privacy(
    setup, tmp_path, monkeypatch, existing, recording
):
    s = setup
    session = async_to_sync(get_container().conversations.repo.get_session)(s.tenant, s.sid)
    identity = agent_registry_key(session)
    target, key = tmp_path / "output.json", tmp_path / "synthetic-key"
    key.write_text("synthetic-test-only")
    old = {identity: {"hash": policy_hash(session), "agent_id": "synthetic"}}
    if existing:
        target.write_text(json.dumps(old))
    writes = []

    def provider(request):
        assert request.url.host == "api.elevenlabs.io"
        if request.method != "GET":
            assert request.method == ("PATCH" if existing else "POST")
            payload = json.loads(request.content)
            assert payload["platform_settings"]["privacy"] == {
                "record_voice": False,
                "retention_days": 7,
            }
            assert payload["platform_settings"]["auth"]["enable_auth"] is True
            writes.append(request.method)
            return httpx.Response(200, json={"agent_id": "synthetic"})
        assert request.url.path == "/v1/convai/agents/synthetic"
        return httpx.Response(
            200,
            json={
                "conversation_config": {"realtime_model": DIRECT_MODEL},
                "platform_settings": {
                    "auth": {"enable_auth": True},
                    "privacy": {"record_voice": recording},
                },
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(**{**kw, "transport": httpx.MockTransport(provider)}),
    )

    def provision():
        call_command(
            "provision_direct_voice",
            str(s.tenant),
            str(s.sid),
            output=str(target),
            voice_id="synthetic",
            key_file=str(key),
        )

    if recording is not False:
        with pytest.raises(CommandError, match="grabaciones"):
            provision()
        assert (json.loads(target.read_text()) if target.exists() else None) == (
            old if existing else None
        )
    else:
        provision()
        saved = json.loads(target.read_text())[identity]
        assert saved["record_voice"] is False and saved["agent_id"] == "synthetic"
        provision()
    assert len(writes) == 1
