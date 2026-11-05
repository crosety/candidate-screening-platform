from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from screening.domain.channels import ChannelName, TemplatePurpose

CapabilityId = Literal[
    "faq.search",
    "openings.find",
    "application.read_mine",
    "interview.book",
    "handoff.request",
    "credential.verify",
    "followup.schedule",
    "candidates.query",
    "analytics.aggregates",
]
ConnectionKind = Literal["ats", "data", "calendar", "geo", "notify", "registry"]
# Platform meaning of a client field, so filters and metrics never hardcode client keys.
FieldSemantic = Literal["", "name", "location", "site", "shift", "availability", "start_date"]
Slug = Field(pattern=r"^[a-z0-9_-]{1,40}$")


class Brief(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = ""
    who: str = ""
    fit: str = ""
    voice: str = ""
    followup: str = ""
    avoid: str = ""


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Slug
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=60)
    text: str
    in_context: bool = True
    mode: Literal["context", "search"] = "context"


class WhatsAppTemplateSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    language: str = Field(min_length=1, max_length=20)
    purpose: TemplatePurpose
    variables: list[str] = []


class WhatsAppChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender_ref: str | None = Field(default=None, pattern=r"^secret://[a-z0-9_-]+/[a-z0-9_-]+$")
    first_contact: Literal["template", "wait"] = "wait"
    templates: list[WhatsAppTemplateSettings] = []


class VoiceChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_ref: str = Field(default="", pattern=r"^(?:secret://[a-z0-9_-]+/[a-z0-9_-]+)?$")
    voice_id: str = ""


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    whatsapp: WhatsAppChannelSettings | None = None
    voice: VoiceChannelSettings | None = None


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Slug
    name: str = Field(min_length=1, max_length=120)
    kind: ConnectionKind
    system: str = Field(min_length=1, max_length=160)
    transport: Literal["native", "mcp"] = "native"
    secret_ref: str | None = None
    permission: Literal["read", "write"] = "read"
    backs: list[CapabilityId] = []
    segments: list[str] = []
    settings: dict[str, str] = {}


class Grants(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversador: list[CapabilityId] = []
    recruiter: list[CapabilityId] = []


class ResultField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    description: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=100)
    ats_field: str = ""
    required: bool = True
    value_type: Literal["text", "boolean", "number", "enum", "date"] = "text"
    choices: list[str] = []
    minimum: float | None = None
    future_date: bool = False
    exclude_if_false: bool = False
    semantic: FieldSemantic = ""

    @model_validator(mode="after")
    def _constraints(self) -> "ResultField":
        if self.value_type == "enum" and not self.choices:
            raise ValueError("Un enumerado necesita opciones.")
        if self.exclude_if_false and self.value_type != "boolean":
            raise ValueError("La exclusión negativa necesita un booleano.")
        if self.future_date and self.value_type != "date":
            raise ValueError("La restricción temporal necesita una fecha.")
        return self


class Autonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: int = Field(ge=0, le=3)
    schedule: int = Field(ge=0, le=3)
    followup: int = Field(ge=0, le=3)
    handoff: int = Field(ge=0, le=3)
    qualify: int = Field(ge=0, le=3)
    reject: int = Field(ge=0, le=2)  # tope de plataforma en la UE
    brief: int = Field(ge=0, le=1)  # el agente solo puede proponer cambios a su perfil


class ReengagementPolicy(BaseModel):
    """Deterministic platform edge for silence; the agent still writes each reminder."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    first_after_minutes: int = Field(default=120, ge=15, le=1440)
    max_attempts: int = Field(default=3, ge=1, le=3)
    quiet_from_hour: int = Field(default=21, ge=0, le=23)
    quiet_until_hour: int = Field(default=9, ge=0, le=23)
    abandon_after_hours: int = Field(default=24, ge=1, le=168)


class RubricItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    description: str = Field(min_length=1, max_length=300)
    weight: float = Field(default=1.0, gt=0, le=5)


class FitCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=200)
    field: str
    operator: Literal["present", "equals", "minimum"] = "present"
    expected: str | float | bool | None = None
    required: bool = False
    weight: float = Field(default=1, gt=0, le=10)


class OfferDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    status: Literal["draft", "open", "paused", "closed"] = "draft"
    description: str = Field(default="", max_length=12000)
    instructions: str = Field(default="", max_length=12000)
    fields: list[ResultField] = Field(default_factory=list, max_length=40)
    # None preserves the resolution of immutable v1/v2 publications.
    fields_mode: Literal["inherit", "custom"] | None = None
    source_ids: list[str] | None = None
    criteria: list[FitCriterion] = Field(default_factory=list, max_length=40)
    capabilities: list[CapabilityId] | None = None
    dimensions: dict[str, str] = Field(default_factory=dict, max_length=12)

    @model_validator(mode="after")
    def consistent(self) -> "OfferDefinition":
        if self.fields_mode == "inherit" and self.fields:
            raise ValueError("No se pueden heredar campos y guardar una copia propia a la vez.")
        if self.source_ids is not None and len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("Hay fuentes repetidas en la oferta.")
        for values in ([f.key for f in self.fields], [c.key for c in self.criteria]):
            if len(set(values)) != len(values):
                raise ValueError("Hay campos o criterios repetidos en la oferta.")
        for criterion in self.criteria:
            if criterion.operator != "present" and criterion.expected is None:
                raise ValueError("El criterio necesita un valor de comparación.")
            if criterion.operator == "minimum" and (
                isinstance(criterion.expected, bool)
                or not isinstance(criterion.expected, (int, float))
            ):
                raise ValueError("El mínimo debe ser numérico.")
        return self


class ProfileContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[2, 3] = 2
    brief: Brief
    operating_model: str = ""
    sources: list[Source] = []
    channels: list[ChannelName] = ["web"]
    channel_settings: ChannelSettings = Field(default_factory=ChannelSettings)
    connections: list[Connection] = []
    grants: Grants
    result_schema: list[ResultField] = []
    autonomy: Autonomy
    rubric: list[RubricItem] = []
    eval_threshold: float = Field(default=80.0, ge=0, le=100)
    offers: list[OfferDefinition] = Field(default_factory=list, max_length=100)
    reengagement: ReengagementPolicy = Field(default_factory=ReengagementPolicy)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_v1(cls, value: Any) -> Any:
        if isinstance(value, dict) and value.get("schema_version", 1) == 1:
            # Upgrade the loaded value; never rewrite a published database row.
            return {**value, "schema_version": 2}
        return value

    @model_validator(mode="after")
    def _unique_ids(self) -> "ProfileContent":
        if self.schema_version == 3 and (self.brief.who or self.brief.fit):
            raise ValueError(
                "Los requisitos del puesto deben estar en la oferta, no en la empresa."
            )
        for name, ids in (
            ("sources", [s.id for s in self.sources]),
            ("connections", [c.id for c in self.connections]),
            ("result_schema", [f.key for f in self.result_schema]),
            ("rubric", [r.key for r in self.rubric]),
            ("channels", self.channels),
            ("offers", [o.key for o in self.offers]),
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(f"Hay identificadores repetidos en {name}")
        for offer in self.offers:
            if self.schema_version == 3 and (offer.fields_mode is None or offer.source_ids is None):
                raise ValueError("La oferta debe indicar el origen de campos y sus fuentes.")
            if offer.source_ids is not None and not set(offer.source_ids).issubset(
                s.id for s in self.sources
            ):
                raise ValueError("La oferta hace referencia a una fuente que no existe.")
            definitions = {f.key: f for f in fields_for_offer(self, offer)}
            keys = set(definitions)
            if any(c.field not in keys for c in offer.criteria):
                raise ValueError("Cada criterio debe referirse a un campo de la oferta.")
            for criterion in offer.criteria:
                kind = definitions[criterion.field].value_type
                if criterion.operator == "minimum" and kind != "number":
                    raise ValueError("Un criterio de mínimo necesita un campo numérico.")
                if criterion.operator == "equals":
                    expected = criterion.expected
                    if (
                        (kind == "boolean" and not isinstance(expected, bool))
                        or (
                            kind == "number"
                            and (
                                isinstance(expected, bool) or not isinstance(expected, (int, float))
                            )
                        )
                        or (kind in ("text", "enum", "date") and not isinstance(expected, str))
                    ):
                        raise ValueError(
                            "El valor del criterio debe tener el mismo tipo que su campo."
                        )
            if offer.capabilities is not None and not set(offer.capabilities).issubset(
                self.grants.conversador
            ):
                raise ValueError("La oferta no puede ampliar las capacidades del cliente.")
        return self


def fields_for_offer(profile: ProfileContent, offer: OfferDefinition) -> list[ResultField]:
    if offer.fields_mode == "custom":
        return offer.fields
    if offer.fields_mode == "inherit":
        return profile.result_schema
    return offer.fields or profile.result_schema


def profile_for_offer(profile: ProfileContent, key: str = "") -> ProfileContent:
    """Resolve only from the session's immutable published profile, never today's draft."""
    offer = next((o for o in profile.offers if o.key == key), None)
    if offer is None:
        return profile
    result = profile.model_copy(deep=True)
    # The offer narrows the client's brief; it never replaces its shared rules.
    result.brief.role = "\n\n".join(
        part for part in (profile.brief.role, f"Oferta: {offer.title}\n{offer.description}") if part
    ).strip()
    if profile.schema_version < 3:
        result.brief.who = profile.brief.who or offer.description
        result.brief.fit = profile.brief.fit or offer.description
    if offer.instructions:
        result.operating_model += f"\n\nInstrucciones de esta oferta:\n{offer.instructions}"
    if offer.criteria:
        criteria = "\n\nCriterios explícitos:\n" + "\n".join(
            f"- {c.label} (campo {c.field}): {c.operator} {c.expected}; "
            f"{'imprescindible' if c.required else 'preferencia'}."
            for c in offer.criteria
        )
        if profile.schema_version == 3:
            result.operating_model += criteria
        else:
            result.brief.fit += criteria
    result.result_schema = [f.model_copy(deep=True) for f in fields_for_offer(profile, offer)]
    if offer.source_ids is not None:
        result.sources = [s for s in result.sources if s.id in offer.source_ids]
    if offer.capabilities is not None:
        result.grants.conversador = offer.capabilities
    # This is an already resolved, serializable runtime view, not an editable
    # catalogue. Keeping unrelated offers would leave dangling source/field refs.
    result.offers = []
    return result


def active_sources(profile: ProfileContent) -> list[Source]:
    """Sources actually available, preserving search semantics of published v1/v2."""
    return [
        s
        for s in profile.sources
        if s.in_context or (profile.schema_version < 3 and s.mode == "search")
    ]
