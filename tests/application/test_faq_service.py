from dataclasses import replace
from uuid import uuid4

import pytest

from screening.application.faq import (
    MAX_CHUNK_CHARS,
    RELEVANCE_THRESHOLD,
    FaqService,
    MemoryFaqStore,
    fragments,
    question_of,
    source_fragments,
)
from screening.domain.profile import ProfileContent, Source
from screening.domain.run_context import RunContext
from tests.application.test_conversation_service import content as content

# asyncio_mode=auto runs the async tests; a module-wide mark would also tag the sync ones.


class Embeddings:
    model_id = "test-semantic-contract"
    dimensions = 2

    async def embed(self, ctx, texts):
        return [[1.0, 0.0] for _ in texts]


def context():
    return RunContext(
        "conversador",
        uuid4(),
        "test",
        "candidate",
        "candidate",
        None,
        None,
        uuid4(),
        frozenset({"faq.search"}),
        {},
    )


def search_source(text: str) -> Source:
    return Source(id="faq", name="Manual", kind="manual", text=text, mode="search")


async def test_chunk_overlap_and_search_never_indexes_context(content: ProfileContent):
    content.sources = [
        Source(id="public", name="En contexto", kind="manual", text="No indexar", mode="context"),
        Source(id="faq", name="Manual", kind="manual", text="x" * 2100, mode="search"),
    ]
    items = fragments(content)
    assert [(x.offset, len(x.text)) for x in items] == [(0, 1000), (800, 1000), (1600, 500)]
    store, ctx = MemoryFaqStore(), context()
    service = FaqService(Embeddings(), store)
    assert await service.index(ctx, content) == 3
    assert (await service.search(ctx, "Pregunta"))["status"] == "ok"
    assert (await service.search(replace(ctx, tenant_id=uuid4()), "Pregunta"))["fragments"] == []
    assert (await service.search(replace(ctx, profile_version_id=uuid4()), "Pregunta"))[
        "fragments"
    ] == []


def test_one_chunk_per_question_without_authoring_metadata():
    text = (
        "<!-- internal: not for candidates -->\n"
        "# Preguntas frecuentes · v1\n"
        "Intro útil.\n\n"
        "# Sección\n"
        "## ¿Necesito moto propia?\nNo, la pone la empresa.\n\n"
        "## Sin respuesta\n   \n"
        "##\t¿Hay turnos?  \nSí.\n### Detalle\nMañana y tarde.\n"
    )
    items = source_fragments(search_source(text))
    assert [x.text for x in items] == [
        "Intro útil.",
        "## ¿Necesito moto propia?\nNo, la pone la empresa.",
        "## ¿Hay turnos?\nSí.\n### Detalle\nMañana y tarde.",
    ]
    assert [x.offset for x in items] == [
        text.index("Intro"),
        text.index("## ¿Necesito"),
        text.index("##\t¿Hay"),
    ]
    assert [question_of(x.text) for x in items] == ["", "¿Necesito moto propia?", "¿Hay turnos?"]
    assert all("internal" not in x.text and "Sección" not in x.text for x in items)


def test_long_answer_is_split_and_every_piece_keeps_its_question():
    answer = "\n\n".join(f"Párrafo {n}. " + "dato " * 60 for n in range(8))
    items = source_fragments(search_source(f"## ¿Pregunta larga?\n{answer}\n## ¿Corta?\nSí."))
    long_items = items[:-1]
    assert len(long_items) > 1 and items[-1].text == "## ¿Corta?\nSí."
    assert all(x.text.startswith("## ¿Pregunta larga?\nPárrafo") for x in long_items)
    assert all(len(x.text) <= MAX_CHUNK_CHARS for x in items)
    offsets = [x.offset for x in items]
    assert offsets[0] == 0 and offsets == sorted(set(offsets))
    rebuilt = " ".join(x.text.split("\n", 1)[1] for x in long_items)
    assert rebuilt.split() == answer.split()


def test_headingless_sources_keep_windows_and_hide_comments():
    text = "<!-- private -->" + "y" * 1500
    items = source_fragments(search_source(text))
    assert [x.offset for x in items] == [0, 800]
    assert all("private" not in x.text and question_of(x.text) == "" for x in items)


async def test_retrieval_threshold_top_three_and_citation():
    class Store(MemoryFaqStore):
        async def search(self, *args):
            scores = (0.99, 0.9, 0.7, RELEVANCE_THRESHOLD, RELEVANCE_THRESHOLD - 0.001)
            return [
                {"source_id": "faq", "source_name": "Manual", "text": f"## ¿P{i}?\nR", "score": x}
                for i, x in enumerate(scores)
            ] + [{"source_id": "old", "source_name": "Antigua", "text": "Ventana", "score": 0.1}]

    result = await FaqService(Embeddings(), Store()).search(context(), "Pregunta")
    assert [row["score"] for row in result["fragments"]] == [0.99, 0.9, 0.7]
    assert [row["citation"] for row in result["fragments"]] == [
        "faq › ¿P0?",
        "faq › ¿P1?",
        "faq › ¿P2?",
    ]

    class Edge(MemoryFaqStore):
        async def search(self, *args):
            return [
                {"source_id": "old", "source_name": "Antigua", "text": "Ventana", "score": 0.8},
                {"source_id": "faq", "source_name": "M", "text": "## ¿P?\nR", "score": 0.4},
                {"source_id": "faq", "source_name": "M", "text": "x", "score": RELEVANCE_THRESHOLD},
            ]

    edge = await FaqService(Embeddings(), Edge()).search(context(), "Pregunta")
    assert [(r["question"], r["citation"]) for r in edge["fragments"]] == [
        ("", "old"),
        ("¿P?", "faq › ¿P?"),
        ("", "faq"),
    ]


async def test_incompatible_embeddings_do_not_replace_index(content: ProfileContent):
    class Broken(Embeddings):
        async def embed(self, *args):
            return [[1.0]]

    content.sources = [Source(id="faq", name="Manual", kind="manual", text="Dato", mode="search")]
    store = MemoryFaqStore()
    service = FaqService(Broken(), store)
    with pytest.raises(ValueError, match="incompatibles"):
        await service.index(context(), content)
    assert store.rows == {}
    with pytest.raises(ValueError, match="incompatibles"):
        await service.search(context(), "Pregunta")


async def test_unknown_version_or_empty_query_never_calls_embeddings():
    class NoCall(Embeddings):
        async def embed(self, *args):
            raise AssertionError("No debe llamar al proveedor")

    service = FaqService(NoCall(), MemoryFaqStore())
    assert (await service.search(context(), " "))["status"] == "unavailable"
    assert (await service.search(replace(context(), profile_version_id=None), "Dato"))[
        "status"
    ] == "unavailable"


async def test_offer_sources_are_filtered_before_ranking(content: ProfileContent):
    content.sources = [
        Source(id=f"doc{i}", name=f"Documento {i}", kind="faq", text="Respuesta", mode="search")
        for i in range(5)
    ]
    service, ctx = FaqService(Embeddings(), MemoryFaqStore()), context()
    await service.index(ctx, content)
    result = await service.search(ctx, "Pregunta", ["doc4"])
    assert [r["source_id"] for r in result["fragments"]] == ["doc4"]
    assert (await service.search(ctx, "Pregunta", []))["fragments"] == []


def test_disabled_sources_are_not_indexed_in_explicit_configuration(content: ProfileContent):
    content.schema_version = 3
    content.sources = [search_source("Dato desactivado").model_copy(update={"in_context": False})]
    assert fragments(content) == []
