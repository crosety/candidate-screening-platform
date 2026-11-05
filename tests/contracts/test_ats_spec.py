"""Offline contract checks for the ATS integration spec (docs/ats/openapi.yaml).

The spec documents what the code does today (``x-status: implemented``) and what a real
integration would add (``x-status: proposed``). These tests keep the implemented part
honest: the ingest examples are replayed against the real endpoint, the write-back
examples are rebuilt with the real application code and the field mapping is compared
with the seed profile. No network, no model calls.
"""

import copy
import functools
import hashlib
import inspect
import json
import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast, get_args
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
import yaml
from django.db.models import Field, Model
from django.test import Client
from django.utils import timezone
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from screening.application.assessment import DecisionBookingPolicy, enqueue_write_result
from screening.application.ports import (
    ConversationRepository,
    DecisionRecord,
    DecisionRepository,
    JobQueue,
    ResultRecord,
)
from screening.domain.autonomy import DecisionType, NextStep
from screening.domain.profile import ProfileContent
from screening.domain.result import EvaluationOutput, FieldValue, StageSentiment, Verdict
from screening.domain.run_context import RunContext
from web.conversations import ingest_auth
from web.conversations.ingest_auth import signature, tenant_key
from web.conversations.schemas import NewApplicationIn

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "docs" / "ats" / "openapi.yaml"
SEED_PROFILE = ROOT / "seeds" / "tenants" / "grupo_sazon" / "profile.json"
SPEC_URI = "urn:screening:ats-spec"
APP_URI = "urn:screening:app-openapi"
INGEST_PATH = "/ingest/{tenant_slug}/applications"
HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
STATUSES = {"implemented", "proposed"}


@functools.cache
def _spec() -> dict[str, Any]:
    with SPEC_PATH.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    assert isinstance(loaded, dict)
    return loaded


def spec() -> dict[str, Any]:
    # Tests receive a private copy so one test can never alter what another one reads.
    return copy.deepcopy(_spec())


def resolve(doc: dict[str, Any], ref: str) -> Any:
    assert ref.startswith("#/"), f"Solo se admiten referencias internas: {ref}"
    node: Any = doc
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        assert isinstance(node, dict) and key in node, f"Referencia rota: {ref}"
        node = node[key]
    return node


def deref(doc: dict[str, Any], node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        node = resolve(doc, node["$ref"])
    return node


def walk(node: Any, path: str = "#") -> Iterator[tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, f"{path}/{index}")


def operations(doc: dict[str, Any]) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for section in ("paths", "webhooks"):
        for name, item in doc.get(section, {}).items():
            for method in HTTP_METHODS:
                if method in item:
                    yield f"{section}:{name}", method, item[method]


def absolute_refs(node: Any, base: str) -> Any:
    if isinstance(node, dict):
        return {
            key: (base + value if key == "$ref" else absolute_refs(value, base))
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [absolute_refs(value, base) for value in node]
    return node


def validator(doc: dict[str, Any], schema: Any, base: str = SPEC_URI) -> Draft202012Validator:
    registry: Registry[Any] = Registry().with_resource(
        base, Resource.from_contents(doc, default_specification=DRAFT202012)
    )
    return Draft202012Validator(
        absolute_refs(schema, base),
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def schema(name: str) -> dict[str, Any]:
    found = spec()["components"]["schemas"][name]
    assert isinstance(found, dict)
    return found


def assert_valid(instance: Any, target: Any, where: str) -> None:
    errors = sorted(validator(spec(), target).iter_errors(instance), key=str)
    assert not errors, f"{where}: {errors[0].message} en {list(errors[0].absolute_path)}"


def ingest_operation() -> dict[str, Any]:
    found = spec()["paths"][INGEST_PATH]["post"]
    assert isinstance(found, dict)
    return found


def request_examples(operation: dict[str, Any]) -> dict[str, Any]:
    media = operation["requestBody"]["content"]["application/json"]
    return {name: example["value"] for name, example in media["examples"].items()}


def webhook_example(event: str, name: str) -> dict[str, Any]:
    operation = spec()["webhooks"][event]["post"]
    value = operation["requestBody"]["content"]["application/json"]["examples"][name]["value"]
    assert isinstance(value, dict)
    return value


# --- structure -------------------------------------------------------------------------


def test_document_is_openapi_3_1_with_complete_operations() -> None:
    doc = spec()
    assert re.fullmatch(r"3\.1\.\d+", doc["openapi"])
    assert doc["info"]["title"] and doc["info"]["version"]
    assert doc["jsonSchemaDialect"] == "https://json-schema.org/draft/2020-12/schema"
    assert doc["paths"] and doc["webhooks"]
    tags = {tag["name"] for tag in doc["tags"]}
    schemes = set(doc["components"]["securitySchemes"])
    seen: dict[str, str] = {}
    for where, method, operation in operations(doc):
        label = f"{where} {method}"
        operation_id = operation.get("operationId")
        assert isinstance(operation_id, str) and operation_id, f"{label} sin operationId"
        assert operation_id not in seen, f"operationId repetido: {operation_id}"
        seen[operation_id] = label
        assert operation.get("responses"), f"{label} sin respuestas"
        assert operation.get("x-status") in STATUSES, f"{label} sin x-status válido"
        assert set(operation.get("tags", [])) <= tags, f"{label} usa etiquetas no declaradas"
        for requirement in operation.get("security", []):
            assert set(requirement) <= schemes, f"{label} usa un esquema no declarado"
        if where.startswith("webhooks:"):
            body = deref(doc, operation.get("requestBody", {}))
            assert body.get("required") is True, f"{label} sin cuerpo obligatorio"
            assert "schema" in body["content"]["application/json"], f"{label} sin esquema"
        else:
            template = set(re.findall(r"{([^}]+)}", where.split(":", 1)[1]))
            declared = {
                param["name"]
                for param in (deref(doc, p) for p in operation.get("parameters", []))
                if param["in"] == "path"
            }
            assert template == declared, f"{label}: parámetros de ruta {template} != {declared}"
    for path, node in walk(doc):
        if isinstance(node, dict) and "x-status" in node:
            assert node["x-status"] in STATUSES, f"x-status desconocido en {path}"


def test_every_ref_resolves() -> None:
    doc = spec()
    refs = [
        node["$ref"]
        for _, node in walk(doc)
        if isinstance(node, dict) and isinstance(node.get("$ref"), str)
    ]
    assert len(refs) > 50
    for ref in refs:
        resolve(doc, ref)


def test_source_pointers_exist() -> None:
    for path, node in walk(spec()):
        if isinstance(node, dict) and "x-source" in node:
            file_name, _, symbol = node["x-source"].partition("::")
            source = ROOT / file_name
            assert source.is_file(), f"{path}: no existe {file_name}"
            text = source.read_text(encoding="utf-8")
            for part in symbol.split("."):
                assert re.search(rf"\b(?:def|class) {re.escape(part)}\b", text), (
                    f"{path}: {symbol} no está en {file_name}"
                )


def collect_examples(doc: dict[str, Any]) -> Iterator[tuple[str, Any, Any]]:
    """Yield (location, schema, example) for every documented example."""
    for path, node in walk(doc):
        if not isinstance(node, dict) or "schema" not in node:
            continue
        if "example" in node:
            yield path, node["schema"], node["example"]
        for name, example in node.get("examples", {}).items():
            example = deref(doc, example)
            if "value" in example:
                yield f"{path}/examples/{name}", node["schema"], example["value"]


def test_every_example_validates_against_its_schema() -> None:
    doc = spec()
    found = list(collect_examples(doc))
    assert len(found) >= 30
    for where, target, example in found:
        assert_valid(example, target, where)


# --- inbound ingest: consistency with the real application ----------------------------


def app_openapi() -> dict[str, Any]:
    from web.api import api

    # Round-trip through JSON: the Ninja schema object is a dict with Python values.
    loaded = json.loads(json.dumps(api.get_openapi_schema(), default=str))
    assert isinstance(loaded, dict)
    return loaded


def api_prefix() -> str:
    return urlsplit(spec()["servers"][0]["url"]).path


def test_ingest_schema_matches_the_application_openapi() -> None:
    app = app_openapi()
    app_operation = app["paths"][api_prefix() + INGEST_PATH]["post"]
    app_request = deref(app, app_operation["requestBody"]["content"]["application/json"]["schema"])
    ours = schema("IngestApplication")
    assert set(ours["properties"]) == set(app_request["properties"])
    assert set(ours["required"]) == set(app_request["required"])
    for name, prop in app_request["properties"].items():
        for keyword in ("type", "minLength", "maxLength", "default"):
            assert ours["properties"][name].get(keyword) == prop.get(keyword), (name, keyword)
    app_response = deref(
        app, app_operation["responses"]["200"]["content"]["application/json"]["schema"]
    )
    ours = schema("IngestResult")
    assert set(ours["properties"]) == set(app_response["properties"])
    assert set(ours["required"]) == set(app_response["required"])
    assert ours["properties"]["application_id"]["format"] == "uuid"
    assert app_response["properties"]["application_id"]["format"] == "uuid"
    app_params = {p["name"] for p in app_operation["parameters"] if p["in"] == "path"}
    assert app_params == {"tenant_slug"}


def test_ingest_examples_are_accepted_by_the_real_request_schema() -> None:
    app = app_openapi()
    app_operation = app["paths"][api_prefix() + INGEST_PATH]["post"]
    target = app_operation["requestBody"]["content"]["application/json"]["schema"]
    check = validator(app, target, APP_URI)
    examples = request_examples(ingest_operation())
    assert examples
    for name, example in examples.items():
        assert not list(check.iter_errors(example)), name
        NewApplicationIn.model_validate(example)


def test_signature_scheme_and_test_vector_match_the_implementation() -> None:
    doc = spec()
    scheme = doc["components"]["securitySchemes"]["ingestSignature"]
    vector = scheme["x-signature-test-vector"]
    body = vector["body"].encode()
    key = tenant_key(vector["root"], vector["tenant_slug"])
    assert key == vector["tenant_key"]
    assert hashlib.sha256(body).hexdigest() == vector["body_sha256"]
    assert vector["path"] == api_prefix() + INGEST_PATH.format(tenant_slug=vector["tenant_slug"])
    assert (
        signature(key, vector["timestamp"], vector["nonce"], vector["path"], body)
        == vector["signature"]
    )
    assert json.loads(body) == request_examples(ingest_operation())["nueva"]
    params = doc["components"]["parameters"]
    patterns = {
        scheme["name"]: scheme["x-pattern"],
        params["IngestTimestamp"]["name"]: params["IngestTimestamp"]["schema"]["pattern"],
        params["IngestNonce"]["name"]: params["IngestNonce"]["schema"]["pattern"],
    }
    source = inspect.getsource(ingest_auth)
    for header, pattern in patterns.items():
        assert f'request.headers.get("{header}"' in source, header
        assert pattern.removeprefix("^").removesuffix("$") in source, (header, pattern)
    assert re.fullmatch(patterns["X-Ingest-Signature"], vector["signature"])
    assert re.fullmatch(patterns["X-Ingest-Timestamp"], vector["timestamp"])
    assert re.fullmatch(patterns["X-Ingest-Nonce"], vector["nonce"])


# --- result mapping and domain vocabulary ----------------------------------------------


def seed_profile() -> ProfileContent:
    return ProfileContent.model_validate_json(SEED_PROFILE.read_text(encoding="utf-8"))


def value_schema(value_type: str) -> dict[str, Any]:
    return {
        "text": {"type": "string"},
        "boolean": {"type": "boolean"},
        "number": {"type": "number"},
        "enum": {"type": "string"},
        "date": {"type": "string", "format": "date"},
    }[value_type]


def test_result_field_mapping_matches_the_seed_profile() -> None:
    fields = seed_profile().result_schema
    result_fields = schema("GrupoSazonResultFields")
    ats_fields = schema("GrupoSazonAtsFields")
    keys = [field.key for field in fields]
    assert list(result_fields["properties"]) == keys
    assert result_fields["required"] == keys
    assert len({field.ats_field for field in fields}) == len(fields), "ats_field repetido"
    assert set(ats_fields["properties"]) == {field.ats_field for field in fields}
    for field in fields:
        documented = result_fields["properties"][field.key]
        assert documented["x-ats-field"] == field.ats_field, field.key
        target = ats_fields["properties"][field.ats_field]
        assert target["x-result-key"] == field.key, field.ats_field
        expected = value_schema(field.value_type)
        assert {k: target.get(k) for k in expected} == expected, field.key
        if field.value_type == "enum":
            assert target["enum"] == field.choices, field.key
            assert documented["properties"]["value"]["enum"] == [*field.choices, None]
        else:
            assert documented["properties"]["value"]["type"] == [expected["type"], "null"]
        assert target.get("minimum") == field.minimum, field.key


def test_the_seven_brief_fields_cover_the_result_schema() -> None:
    brief = spec()["x-brief-fields"]
    keys = [field.key for field in seed_profile().result_schema]
    assert len(brief) == 7
    covered = [key for entry in brief for key in entry["result_keys"]]
    assert sorted(covered) == sorted(keys), "cada clave pertenece a un solo campo de cribado"
    owner = {key: entry["name"] for entry in brief for key in entry["result_keys"]}
    for key, documented in schema("GrupoSazonResultFields")["properties"].items():
        assert documented["x-brief-field"] == owner[key], key


def literal_values(annotation: Any) -> list[Any]:
    while not all(isinstance(arg, str) for arg in get_args(annotation)):
        (annotation,) = (arg for arg in get_args(annotation) if get_args(arg))
    return list(get_args(annotation))


def choice_values(model: type[Model], name: str) -> list[Any]:
    field = model._meta.get_field(name)
    assert isinstance(field, Field)
    return [value for value, _ in field.choices or []]


def test_domain_vocabulary_matches_the_code() -> None:
    from web.conversations.models import Session
    from web.decisions.models import Decision
    from web.jobs.models import Job

    assert schema("Verdict")["enum"] == list(get_args(Verdict))
    flags = literal_values(EvaluationOutput.model_fields["flags"].annotation)
    assert schema("ResultFlag")["enum"] == [*flags, "evaluation_failed", "ats_not_writable"]
    confidence = FieldValue.model_fields["confidence"].annotation
    assert schema("FieldValue")["properties"]["confidence"]["enum"] == list(get_args(confidence))
    sentiment = schema("StageSentiment")["properties"]
    for name in ("stage", "label"):
        annotation = StageSentiment.model_fields[name].annotation
        assert sentiment[name]["enum"] == list(get_args(annotation)), name
    decision = schema("DecisionRef")["properties"]
    assert decision["decision_type"]["enum"] == list(get_args(DecisionType))
    assert decision["status"]["enum"] == choice_values(Decision, "status")
    completed = schema("ScreeningCompletedEvent")["properties"]["data"]["properties"]
    assert completed["next_step"]["enum"] == list(get_args(NextStep))
    session = schema("SessionRef")["properties"]
    assert session["channel"]["enum"] == choice_values(Session, "channel")
    assert session["status"]["enum"] == choice_values(Session, "status")
    delivery = spec()["x-delivery"]["implemented"]
    assert delivery["max_attempts"] == Job._meta.get_field("max_attempts").default


# --- write-back: the examples are what the code sends ----------------------------------


class Recorder:
    """Stand-in for the few repository and queue methods the write-back code calls."""

    def __init__(self, external_id: str) -> None:
        self.application = SimpleNamespace(id=uuid4(), external_id=external_id)
        self.outbox: list[tuple[str, dict[str, Any], str]] = []

    async def get_session(self, tenant_id: UUID, session_id: UUID) -> SimpleNamespace:
        return SimpleNamespace(id=session_id, application_id=self.application.id)

    async def get_application(self, tenant_id: UUID, application_id: UUID) -> SimpleNamespace:
        return self.application

    async def create_outbox(
        self, tenant_id: UUID, session_id: UUID, kind: str, payload: dict[str, Any], key: str
    ) -> SimpleNamespace:
        self.outbox.append((kind, copy.deepcopy(payload), key))
        return SimpleNamespace(id=uuid4())

    async def enqueue(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def set_session_status(self, *args: Any) -> None:
        return None

    # The ports are wide protocols; the code under test only calls the methods above.
    @property
    def decisions(self) -> DecisionRepository:
        return cast(DecisionRepository, self)

    @property
    def conversations(self) -> ConversationRepository:
        return cast(ConversationRepository, self)

    @property
    def queue(self) -> JobQueue:
        return cast(JobQueue, self)


async def test_write_result_example_is_what_the_outbox_receives() -> None:
    event = webhook_example("screening.completed", "apta")
    data = event["data"]
    assert_valid(data["result"]["fields"], schema("GrupoSazonResultFields"), "resultado")
    recorder = Recorder(event["application"]["external_id"])
    session_id = UUID(data["session"]["id"])
    tenant_id, now = uuid4(), datetime.now(UTC)
    decision = DecisionRecord(
        UUID(data["decision"]["id"]),
        tenant_id,
        session_id,
        data["decision"]["decision_type"],
        data["decision"]["status"],
        data["decision"]["autonomy_level"],
        data["decision"]["decided_by"],
        None,
        data["result"]["reason"],
        now,
        now,
    )
    result = ResultRecord(
        session_id,
        data["result"]["verdict"],
        data["result"]["fields"],
        data["result"]["flags"],
        data["result"]["summary"],
        data["result"]["reason"],
        data["result"]["evaluator_model"],
        data["result"]["sentiment"],
    )
    # The recorder plays every port; the call itself is the real application code.
    await enqueue_write_result(
        recorder.decisions,
        recorder.conversations,
        recorder.queue,
        tenant_id,
        session_id,
        decision,
        data["result"]["verdict"],
        result,
        seed_profile(),
    )
    ((kind, payload, key),) = recorder.outbox
    operation = spec()["webhooks"]["ats.write_result"]["post"]
    assert kind == operation["x-outbox-kind"]
    assert key == operation["x-idempotency-key-template"].format(decision_id=decision.id)
    assert payload == webhook_example("ats.write_result", "apta")
    assert payload["fields"] == data["ats_fields"]
    assert_valid(payload, schema("AtsWriteResultCommand"), "write_result")
    assert_valid(payload["fields"], schema("GrupoSazonAtsFields"), "write_result.fields")


async def test_set_stage_example_is_what_a_booking_enqueues() -> None:
    booked = webhook_example("interview.booked", "reservada")
    interview = booked["data"]["interview"]
    recorder = Recorder(booked["application"]["external_id"])
    session_id = UUID(booked["data"]["session_id"])
    ctx = RunContext(
        "conversador",
        uuid4(),
        "grupo_sazon",
        "tenant",
        "candidate",
        session_id,
        uuid4(),
        uuid4(),
        frozenset(),
        {},
        offer_key="repartidores",
    )
    policy = DecisionBookingPolicy(recorder.decisions, recorder.conversations, recorder.queue)
    await policy.booked(
        ctx,
        seed_profile(),
        {"status": "booked", **{k: interview[k] for k in ("starts_at_local", "site_name")}},
    )
    ((kind, payload, key),) = recorder.outbox
    operation = spec()["webhooks"]["ats.set_stage"]["post"]
    assert kind == operation["x-outbox-kind"]
    assert key == operation["x-idempotency-key-template"].format(session_id=session_id)
    assert payload == webhook_example("ats.set_stage", "entrevista")
    assert payload["stage"] == booked["data"]["stage"]
    assert_valid(payload, schema("AtsSetStageCommand"), "set_stage")


# --- ingest responses replayed against the real endpoint -------------------------------


def documented_ingest_errors() -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for status, response in _spec()["paths"][INGEST_PATH]["post"]["responses"].items():
        for media in response.get("content", {}).values():
            for name, example in media.get("examples", {}).items():
                if "x-trigger" in example:
                    found.append((status, name, example))
    return found


def test_implemented_and_proposed_ingest_responses_are_marked() -> None:
    responses = ingest_operation()["responses"]
    triggered = {status for status, _, _ in documented_ingest_errors()}
    for status, response in responses.items():
        assert response["x-status"] in STATUSES, status
        if response["x-status"] == "implemented" and status != "200":
            assert status in triggered, f"{status} implementado sin ejemplo reproducido"
        if response["x-status"] == "proposed":
            assert status not in triggered, f"{status} propuesto con ejemplo reproducido"
            assert "application/problem+json" in response["content"], status
    required = {"400", "401", "409", "410", "422", "429", "503"}
    assert required <= set(responses)
    implemented = {s for s, r in responses.items() if r["x-status"] == "implemented"}
    assert implemented == {"200", "400", "401", "404", "409", "503"}


class IngestCaller:
    def __init__(self, client: Client, root: str) -> None:
        self.client, self.root = client, root

    def post(
        self,
        payload: dict[str, Any],
        *,
        slug: str = "grupo_sazon",
        signed: bool = True,
        root: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[Any, dict[str, str]]:
        path = api_prefix() + INGEST_PATH.format(tenant_slug=slug)
        body = json.dumps(payload).encode()
        if headers is None:
            headers = {}
            if signed:
                timestamp, nonce = str(int(time.time())), uuid4().hex
                key = tenant_key(root or self.root, slug)
                headers = {
                    "X-Ingest-Timestamp": timestamp,
                    "X-Ingest-Nonce": nonce,
                    "X-Ingest-Signature": signature(key, timestamp, nonce, path, body),
                }
        response = self.client.post(path, body, content_type="application/json", headers=headers)
        return response, headers


@pytest.fixture
def ingest_caller(settings: Any) -> IngestCaller:
    from web.tenancy.models import Tenant

    vector = spec()["components"]["securitySchemes"]["ingestSignature"]["x-signature-test-vector"]
    settings.INGEST_TOKEN = vector["root"]
    settings.META_SEND_ENABLED = False
    Tenant.objects.create(slug=vector["tenant_slug"], name="Grupo Sazón")
    return IngestCaller(Client(), vector["root"])


class BrokenCache:
    def add(self, *args: Any, **kwargs: Any) -> bool:
        raise ConnectionError("cache unavailable")


def reproduce(
    trigger: str, example: dict[str, Any], caller: IngestCaller, monkeypatch: pytest.MonkeyPatch
) -> Any:
    from web.conversations.models import Application, CandidateLink
    from web.tenancy.context import tenant_context
    from web.tenancy.models import Tenant

    valid = request_examples(ingest_operation())["nueva"]
    if trigger == "invalid_body":
        return caller.post(example["x-request-body"], signed=example["x-signed"])[0]
    if trigger == "bad_signature":
        return caller.post(valid, root="otra-raiz-de-ejemplo")[0]
    if trigger == "unknown_tenant":
        return caller.post(valid, slug="cliente_inexistente")[0]
    if trigger == "cache_unavailable":
        monkeypatch.setattr(ingest_auth, "cache", BrokenCache())
        return caller.post(valid)[0]
    first, headers = caller.post(valid)
    assert first.status_code == 200
    if trigger == "replay":
        return caller.post(valid, headers=headers)[0]
    if trigger == "job_ref_changed":
        return caller.post({**valid, "job_ref": "cocina"})[0]
    tenant = Tenant.objects.get(slug="grupo_sazon")
    with tenant_context(tenant.id):
        rows = Application.objects.filter(tenant=tenant, external_id=valid["external_id"])
        if trigger == "erased":
            rows.update(erased_at=timezone.now())
        elif trigger == "link_needs_renewal":
            CandidateLink.objects.filter(application_id__in=rows.values("id")).update(
                revoked_at=timezone.now()
            )
        else:
            raise AssertionError(f"Disparador sin escenario: {trigger}")
    return caller.post(valid)[0]


@pytest.mark.django_db(transaction=True)
def test_success_example_shape_and_idempotent_recovery(
    ingest_caller: IngestCaller, settings: Any
) -> None:
    examples = request_examples(ingest_operation())
    first, _ = ingest_caller.post(examples["nueva"])
    assert first.status_code == 200
    assert_valid(first.json(), schema("IngestResult"), "respuesta real")
    assert first.json()["candidate_link"].startswith(f"{settings.CHAT_BASE_URL}/c/")
    again, _ = ingest_caller.post(examples["nueva"])
    assert again.status_code == 200
    assert again.json() == first.json()
    minimal, _ = ingest_caller.post(examples["minima"])
    assert minimal.status_code == 200
    assert minimal.json()["application_id"] != first.json()["application_id"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("status", "name", "example"),
    documented_ingest_errors(),
    ids=[f"{status}-{name}" for status, name, _ in documented_ingest_errors()],
)
def test_documented_ingest_error_is_what_the_code_returns(
    ingest_caller: IngestCaller,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    name: str,
    example: dict[str, Any],
) -> None:
    response = reproduce(example["x-trigger"], example, ingest_caller, monkeypatch)
    assert response.status_code == int(status), response.content
    assert response.json() == example["value"]
