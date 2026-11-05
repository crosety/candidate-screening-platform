"""Offline FAQ retrieval eval with recorded text-embedding-3-small vectors; no network."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from adapters.llm.embeddings import OpenAIEmbeddings
from screening.application.faq import FaqService, MemoryFaqStore, source_fragments
from screening.domain.profile import ProfileContent, Source
from screening.domain.run_context import RunContext
from tests.application.test_conversation_service import content as content

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "tests/fixtures/faq_eval.json").read_text(encoding="utf-8"))
RECORDED = json.loads((ROOT / "tests/fixtures/faq_embeddings.json").read_text(encoding="utf-8"))
RERECORD = "Regraba con `uv run python tools/record_faq_embeddings.py` (docs/faq-rag.md)."
# Floors set from the measured run of 28/10/2026 (hit@3 27/28, no-answer 1/5). The vectors
# are recorded, so any drop is a real regression in chunking, threshold or FAQ content.
HIT_AT_3_FLOOR = 0.96
NO_ANSWER_FLOOR = 0.2


def key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def faq_source() -> Source:
    spec = SPEC["source"]
    return Source(
        id=spec["id"],
        name=spec["name"],
        kind=spec["kind"],
        text=(ROOT / spec["path"]).read_text(encoding="utf-8"),
        mode="search",
    )


class RecordedEmbeddings:
    model_id = OpenAIEmbeddings.model_id
    dimensions = OpenAIEmbeddings.dimensions

    async def embed(self, ctx: RunContext, texts: list[str]) -> list[list[float]]:
        vectors: dict[str, list[float]] = RECORDED["vectors"]
        missing = [text[:60] for text in texts if key(text) not in vectors]
        if missing:
            pytest.fail(f"Sin embedding grabado para {missing}. {RERECORD}")
        return [vectors[key(text)] for text in texts]


def test_recorded_vectors_match_current_faq_and_queries() -> None:
    assert RECORDED["model"] == OpenAIEmbeddings.model_id
    assert RECORDED["dimensions"] == OpenAIEmbeddings.dimensions
    texts = [f.text for f in source_fragments(faq_source())] + [q["query"] for q in SPEC["queries"]]
    wanted, stored = {key(t) for t in texts}, set(RECORDED["vectors"])
    changed = [t[:60] for t in texts if key(t) not in stored]
    assert not changed, f"El texto de la FAQ o del eval ha cambiado: {changed}. {RERECORD}"
    assert stored == wanted, f"Vectores obsoletos en el fixture. {RERECORD}"
    assert all(len(v) == OpenAIEmbeddings.dimensions for v in RECORDED["vectors"].values())


def test_shipped_profile_uses_the_evaluated_faq() -> None:
    profile = json.loads(
        (ROOT / "seeds/tenants/grupo_sazon/profile.json").read_text(encoding="utf-8")
    )
    shipped = [s for s in profile["sources"] if s["id"] == SPEC["source"]["id"]]
    assert len(shipped) == 1, "El perfil de la demo debe incluir la FAQ evaluada."
    for source in shipped:
        assert source.get("mode") == "search"
        assert [f.text for f in source_fragments(Source.model_validate(source))] == [
            f.text for f in source_fragments(faq_source())
        ], "La FAQ del perfil no coincide con la evaluada; re-embébela desde el .md."


async def test_hit_at_3_and_no_answer_precision(content: ProfileContent) -> None:
    content.sources = [faq_source()]
    ctx = RunContext(
        "conversador",
        uuid4(),
        "grupo_sazon",
        "candidate",
        "candidate",
        None,
        None,
        uuid4(),
        frozenset({"faq.search"}),
        {},
    )
    service = FaqService(RecordedEmbeddings(), MemoryFaqStore())
    assert await service.index(ctx, content) == len(source_fragments(faq_source()))
    hits, answerable, rejected, unanswerable, misses = 0, 0, 0, 0, []
    for query in SPEC["queries"]:
        result = await service.search(ctx, query["query"])
        fragments = result["fragments"]
        assert isinstance(fragments, list) and len(fragments) <= 3
        found = [str(f["question"]) for f in fragments]
        if query["expected"]:
            answerable += 1
            hit = bool(set(found) & set(query["expected"]))
            hits += hit
            if not hit:
                misses.append((query["id"], found))
        else:
            unanswerable += 1
            rejected += result["status"] == "no_relevant_evidence"
            if fragments:
                misses.append((query["id"], found))
    hit_at_3, no_answer = hits / answerable, rejected / unanswerable
    report = f"hit@3={hits}/{answerable} no-answer={rejected}/{unanswerable} fallos={misses}"
    assert hit_at_3 >= HIT_AT_3_FLOOR, report
    assert no_answer >= NO_ANSWER_FLOOR, report
