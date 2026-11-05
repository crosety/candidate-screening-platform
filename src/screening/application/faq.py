import math
import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from screening.domain.profile import ProfileContent, Source, active_sources
from screening.domain.run_context import RunContext

# Legacy windows for search sources without "## " questions (overlap of 200 characters).
WINDOW_CHARS = 1000
WINDOW_STRIDE = 800
# A question chunk is "## heading" + answer; only longer answers are cut, and every piece
# repeats the heading so it can be cited on its own.
MAX_CHUNK_CHARS = 1000
MIN_PIECE_CHARS = 200
TOP_K = 3
# Cosine floor for text-embedding-3-small, measured on tests/fixtures/faq_eval.json: any
# value in 0.30-0.41 gives the same result (hit@3 27/28). 0.35 sits between off-topic
# queries (<= 0.24) and the weakest correct match (0.41). In-domain questions the FAQ does
# not answer score 0.43-0.53, like real matches: the reader must still not invent.
RELEVANCE_THRESHOLD = 0.35

_QUESTION = re.compile(r"^##[ \t]+(\S[^\n]*?)[ \t\r]*$", re.MULTILINE)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TITLE = re.compile(r"^#[ \t][^\n]*$", re.MULTILINE)
_SEPARATORS = ("\n\n", "\n", ". ", " ")


@dataclass(frozen=True)
class Fragment:
    source_id: str
    source_name: str
    offset: int
    text: str


class Embeddings(Protocol):
    model_id: str
    dimensions: int

    async def embed(self, ctx: RunContext, texts: list[str]) -> list[list[float]]: ...


class FaqStore(Protocol):
    async def replace(
        self,
        tenant_id: UUID,
        version_id: UUID,
        fragments: list[Fragment],
        vectors: list[list[float]],
        model_id: str,
    ) -> None: ...
    async def search(
        self,
        tenant_id: UUID,
        version_id: UUID,
        vector: list[float],
        model_id: str,
        source_ids: list[str] | None = None,
    ) -> list[dict[str, str | float]]: ...


def _blank(match: re.Match[str]) -> str:
    # Same length and line breaks: offsets keep pointing into the original source text.
    return re.sub(r"[^\n]", " ", match.group())


def _split(text: str, start: int, end: int, budget: int) -> list[tuple[int, str]]:
    """Cut text[start:end] at paragraph, line, sentence or word boundaries."""
    while end > start and text[end - 1].isspace():
        end -= 1
    pieces: list[tuple[int, str]] = []
    pos = start
    while True:
        while pos < end and text[pos].isspace():
            pos += 1
        if pos >= end:
            return pieces
        cut = min(pos + budget, end)
        if cut < end:
            window = text[pos:cut]
            for separator in _SEPARATORS:
                found = window.rfind(separator)
                if found >= budget // 2:
                    cut = pos + found + len(separator)
                    break
        pieces.append((pos, text[pos:cut].rstrip()))
        pos = cut


def _section(
    source: Source, text: str, question: re.Match[str] | None, start: int, end: int
) -> list[Fragment]:
    heading = f"## {question.group(1)}" if question else ""
    budget = max(MAX_CHUNK_CHARS - len(heading) - 1, MIN_PIECE_CHARS)
    pieces = _split(text, start, end, budget if heading else MAX_CHUNK_CHARS)
    if question and pieces:
        pieces[0] = (question.start(), pieces[0][1])  # the chunk starts at its heading
    return [
        Fragment(source.id, source.name, offset, f"{heading}\n{piece}" if heading else piece)
        for offset, piece in pieces
    ]


def source_fragments(source: Source) -> list[Fragment]:
    """Retrieval chunks of one source. HTML comments are authoring notes, never indexed."""
    text = _COMMENT.sub(_blank, source.text)
    questions = list(_QUESTION.finditer(text))
    if not questions:
        return [
            Fragment(source.id, source.name, offset, chunk)
            for offset in range(0, len(text), WINDOW_STRIDE)
            if (chunk := text[offset : offset + WINDOW_CHARS].strip())
        ]
    # In a question document "# " lines are titles or section dividers, not answers.
    text = _TITLE.sub(_blank, text)
    result = _section(source, text, None, 0, questions[0].start())
    for current, following in zip(questions, [*questions[1:], None], strict=True):
        end = following.start() if following else len(text)
        result += _section(source, text, current, current.end(), end)
    return result


def fragments(profile: ProfileContent) -> list[Fragment]:
    return [
        fragment
        for source in active_sources(profile)
        if source.mode == "search"
        for fragment in source_fragments(source)
    ]


def question_of(text: str) -> str:
    """Question heading a chunk answers; empty for legacy window chunks."""
    first = text.split("\n", 1)[0]
    return first[3:].strip() if first.startswith("## ") else ""


def cited(row: dict[str, str | float]) -> dict[str, str | float]:
    """Citation = source id + question heading, rebuilt from the stored chunk text."""
    source, question = str(row.get("source_id", "")), question_of(str(row.get("text", "")))
    return {
        **row,
        "question": question,
        "citation": f"{source} › {question}" if question else source,
    }


class FaqService:
    def __init__(self, embeddings: Embeddings, store: FaqStore):
        self.embeddings, self.store = embeddings, store

    async def index(self, ctx: RunContext, profile: ProfileContent) -> int:
        if ctx.profile_version_id is None:
            raise ValueError("La indexación necesita una versión.")
        items = fragments(profile)
        if len(items) > 100:
            raise ValueError("Máximo cien fragmentos por indexación.")
        vectors = await self.embeddings.embed(ctx, [i.text for i in items]) if items else []
        if len(vectors) != len(items) or any(len(v) != self.embeddings.dimensions for v in vectors):
            raise ValueError("El proveedor devolvió embeddings incompletos o incompatibles.")
        await self.store.replace(
            ctx.tenant_id, ctx.profile_version_id, items, vectors, self.embeddings.model_id
        )
        return len(items)

    async def search(
        self, ctx: RunContext, query: str, source_ids: list[str] | None = None
    ) -> dict[str, object]:
        if ctx.profile_version_id is None or not query.strip() or len(query) > 500:
            return {"status": "unavailable", "fragments": []}
        if source_ids == []:
            return {"status": "no_relevant_evidence", "fragments": []}
        vectors = await self.embeddings.embed(ctx, [query])
        if len(vectors) != 1 or len(vectors[0]) != self.embeddings.dimensions:
            raise ValueError("El proveedor devolvió embeddings incompletos o incompatibles.")
        rows = await self.store.search(
            ctx.tenant_id, ctx.profile_version_id, vectors[0], self.embeddings.model_id, source_ids
        )
        relevant = [cited(r) for r in rows if float(r["score"]) >= RELEVANCE_THRESHOLD][:TOP_K]
        return {
            "status": "ok" if relevant else "no_relevant_evidence",
            "fragments": relevant,
            "instruction": "Cita la fuente (citation); sin evidencia, no inventes la respuesta.",
        }


class MemoryFaqStore:
    """Per-eval index: same embeddings and retrieval, no writes to live versions."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID, str], list[tuple[Fragment, list[float]]]] = {}

    async def replace(
        self,
        tenant_id: UUID,
        version_id: UUID,
        fragments: list[Fragment],
        vectors: list[list[float]],
        model_id: str,
    ) -> None:
        self.rows[(tenant_id, version_id, model_id)] = list(zip(fragments, vectors, strict=True))

    async def search(
        self,
        tenant_id: UUID,
        version_id: UUID,
        vector: list[float],
        model_id: str,
        source_ids: list[str] | None = None,
    ) -> list[dict[str, str | float]]:
        result: list[dict[str, str | float]] = []
        for fragment, stored in self.rows.get((tenant_id, version_id, model_id), []):
            if source_ids is not None and fragment.source_id not in source_ids:
                continue
            norm = math.sqrt(sum(x * x for x in vector) * sum(x * x for x in stored))
            score = sum(a * b for a, b in zip(vector, stored, strict=True)) / norm if norm else 0.0
            result.append(
                {
                    "source_id": fragment.source_id,
                    "source_name": fragment.source_name,
                    "text": fragment.text,
                    "score": score,
                }
            )
        return sorted(result, key=lambda r: float(r["score"]), reverse=True)[:TOP_K]
