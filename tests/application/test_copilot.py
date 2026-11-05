import json

import pytest

from adapters.fakes.copilot import ScriptedCopilot, StaticCopilotMaterialReader
from screening.application.copilot import CopilotService
from screening.application.ports import CopilotMaterial, Suggestion
from screening.domain.errors import NotAllowed
from tests.application.test_conversation_service import Bundle
from tests.application.test_conversation_service import bundle as bundle
from tests.application.test_conversation_service import content as content

pytestmark = pytest.mark.asyncio


async def test_filters_caps_and_context(bundle: Bundle, caplog: pytest.LogCaptureFixture) -> None:
    proposals = [
        Suggestion(field=k, why="Caso observado", append=text)
        for k, text in [
            ("operating_model", "No es brief"),
            ("role", "  "),
            ("fit", "Uno"),
            ("voice", "Dos"),
            ("who", "Tres"),
            ("avoid", "Cuatro"),
        ]
    ]
    copilot = ScriptedCopilot(proposals)
    service = CopilotService(
        bundle.service.versions,
        bundle.repo,
        StaticCopilotMaterialReader(CopilotMaterial([], [], [{"score": 40}])),
        copilot,
    )
    before = (await bundle.service.versions.draft(bundle.tenant)).content.model_dump()
    assert [s.append for s in await service.suggestions(bundle.tenant, "brief")] == [
        "Uno",
        "Dos",
        "Tres",
    ]
    ctx, section, material = copilot.calls[0]
    assert ctx.agent == "copiloto" and ctx.talks_to == "fde" and not ctx.granted
    assert ctx.session_id is None and ctx.application_id is None and ctx.profile_version_id is None
    assert section == "brief" and json.loads(material)["eval_cases"] == [{"score": 40}]
    assert (await bundle.service.versions.draft(bundle.tenant)).content.model_dump() == before
    assert [s.field for s in await service.suggestions(bundle.tenant, "operating_model")] == [
        "operating_model"
    ]
    assert "reason=wrong_section" in caplog.text
    assert "reason=empty_content" in caplog.text
    assert "reason=limit" in caplog.text
    assert "No es brief" not in caplog.text


async def test_empty_and_invalid_section(bundle: Bundle) -> None:
    service = CopilotService(
        bundle.service.versions, bundle.repo, StaticCopilotMaterialReader(), ScriptedCopilot()
    )
    assert await service.suggestions(bundle.tenant, "brief") == []
    with pytest.raises(NotAllowed):
        await service.suggestions(bundle.tenant, "connections")


async def test_v3_copilot_proposes_only_shared_fields_with_offer_context(bundle: Bundle) -> None:
    from screening.domain.profile import OfferDefinition

    draft = bundle.profiles.get_draft(bundle.tenant)
    draft.content.schema_version = 3
    draft.content.brief.who = draft.content.brief.fit = ""
    draft.content.offers = [
        OfferDefinition(
            key="cook",
            title="Cocinero",
            fields_mode="custom",
            source_ids=[],
            description="Dos años de cocina",
        )
    ]
    bundle.profiles.save_draft(bundle.tenant, draft.rev, draft.content, bundle.user)
    copilot = ScriptedCopilot(
        [
            Suggestion(field="who", why="Caso", append="No es común"),
            Suggestion(field="fit", why="Caso", append="No es común"),
            Suggestion(field="voice", why="Caso", append="Pregunta de una en una"),
        ]
    )
    service = CopilotService(
        bundle.service.versions, bundle.repo, StaticCopilotMaterialReader(), copilot
    )
    assert [s.field for s in await service.suggestions(bundle.tenant, "brief")] == ["voice"]
    material = json.loads(copilot.calls[0][2])
    assert material["offers"][0]["description"] == "Dos años de cocina"
    assert "who" not in material["allowed_brief_fields"]
