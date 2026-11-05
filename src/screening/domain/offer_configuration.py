"""Explicit, reviewed preparation of a new draft; never migrates published records."""

from pydantic import BaseModel, ConfigDict

from screening.domain.profile import CapabilityId, ProfileContent, fields_for_offer


class OfferScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ids: list[str]
    capabilities: list[CapabilityId] | None = None
    move_legacy_requirements: bool = False


class ConfigurationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_description: str
    company_instructions: str
    offers: dict[str, OfferScope]


def prepare_configuration(profile: ProfileContent, plan: ConfigurationPlan) -> ProfileContent:
    if profile.schema_version == 3:
        return profile.model_copy(deep=True)
    if set(plan.offers) != {o.key for o in profile.offers}:
        raise ValueError(
            "El plan debe clasificar todas las ofertas, sin añadir ni eliminar ninguna."
        )
    data = profile.model_dump()
    data["schema_version"] = 3
    data["brief"].update(role=plan.company_description, who="", fit="")
    data["operating_model"] = plan.company_instructions
    data["result_schema"] = []
    for original, offer in zip(profile.offers, data["offers"], strict=True):
        scope = plan.offers[original.key]
        offer["fields"] = [f.model_dump() for f in fields_for_offer(profile, original)]
        offer["fields_mode"] = "custom"
        offer["source_ids"] = scope.source_ids
        if scope.capabilities is not None:
            offer["capabilities"] = scope.capabilities
        if scope.move_legacy_requirements:
            offer["description"] = "\n\n".join(
                part
                for part in (original.description, profile.brief.who, profile.brief.fit)
                if part
            )
            offer["instructions"] = "\n\n".join(
                part for part in (original.instructions, profile.operating_model) if part
            )
    return ProfileContent.model_validate(data)
