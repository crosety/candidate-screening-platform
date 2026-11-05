"""Record real embeddings for the offline FAQ retrieval eval (tests/fixtures/faq_*.json).

Calls only the OpenAI embeddings endpoint, never a chat model, and only for texts whose
vector is missing. The API key is read from the file named by OPENAI_API_KEY_FILE
(default: .runtime-secrets/openai_api_key); it is never printed, logged or written.

    uv run python tools/record_faq_embeddings.py           # record missing vectors
    uv run python tools/record_faq_embeddings.py --check   # offline: is the fixture current?
"""

import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

import openai

from adapters.llm.embeddings import OpenAIEmbeddings
from screening.application.faq import source_fragments
from screening.domain.profile import Source

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "tests/fixtures/faq_eval.json"
FIXTURE = ROOT / "tests/fixtures/faq_embeddings.json"
DEFAULT_KEY_FILE = ROOT / ".runtime-secrets/openai_api_key"
DECIMALS = 5
USD_PER_MILLION_TOKENS = 0.02  # text-embedding-3-small list price


def text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def eval_texts() -> list[str]:
    """Every chunk of the evaluated FAQ plus every eval query, in a stable order."""
    spec = json.loads(EVAL.read_text())
    source = Source(
        id=spec["source"]["id"],
        name=spec["source"]["name"],
        kind=spec["source"]["kind"],
        text=(ROOT / spec["source"]["path"]).read_text(encoding="utf-8"),
        mode="search",
    )
    texts = [fragment.text for fragment in source_fragments(source)]
    texts += [str(query["query"]) for query in spec["queries"]]
    return list(dict.fromkeys(texts))


def api_key() -> str:
    path = Path(os.environ.get("OPENAI_API_KEY_FILE", str(DEFAULT_KEY_FILE)))
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise SystemExit("No se puede leer el fichero de la clave de OpenAI.") from None
    if not key:
        raise SystemExit("El fichero de la clave de OpenAI está vacío.")
    return key


def embed(texts: list[str]) -> tuple[list[list[float]], int]:
    client = openai.OpenAI(api_key=api_key(), timeout=30, max_retries=1)
    try:
        response = client.embeddings.create(
            model=OpenAIEmbeddings.model_id,
            input=texts,
            dimensions=OpenAIEmbeddings.dimensions,
            encoding_format="float",
        )
    except openai.APIError as exc:
        # Provider messages may echo part of the credential: report only the error type.
        raise SystemExit(f"La API de embeddings ha fallado ({type(exc).__name__}).") from None
    vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    if len(vectors) != len(texts) or any(len(v) != OpenAIEmbeddings.dimensions for v in vectors):
        raise SystemExit("La API devolvió un número o una dimensión de embeddings inesperados.")
    return vectors, response.usage.prompt_tokens


def write(vectors: dict[str, list[float]], tokens_spent: int, recorded_at: str) -> None:
    header = {
        "model": OpenAIEmbeddings.model_id,
        "dimensions": OpenAIEmbeddings.dimensions,
        "decimals": DECIMALS,
        "recorded_at": recorded_at,
        "tokens_spent": tokens_spent,
        "key": "sha256 of the exact UTF-8 text embedded",
    }
    head = [
        f'  "{name}": {json.dumps(value, ensure_ascii=False)}' for name, value in header.items()
    ]
    rows = [
        f'    "{key}": {json.dumps(vector, separators=(",", ":"))}'
        for key, vector in sorted(vectors.items())
    ]
    body = ",\n".join([*head, '  "vectors": {\n' + ",\n".join(rows) + "\n  }"])
    FIXTURE.write_text("{\n" + body + "\n}\n", encoding="utf-8")


def main() -> None:
    texts = eval_texts()
    stored: dict[str, object] = json.loads(FIXTURE.read_text()) if FIXTURE.exists() else {}
    current = stored.get("vectors", {})
    known: dict[str, list[float]] = current if isinstance(current, dict) else {}
    wanted = {text_key(text): text for text in texts}
    missing = [text for key, text in wanted.items() if key not in known]
    stale = sorted(set(known) - set(wanted))
    if "--check" in sys.argv[1:]:
        if missing or stale:
            raise SystemExit(
                f"Fixture desactualizado: {len(missing)} sin vector, {len(stale)} sobran."
            )
        print(f"Fixture al día: {len(wanted)} textos.")
        return
    tokens = int(str(stored.get("tokens_spent", 0)))
    recorded_at = str(stored.get("recorded_at", ""))
    new_tokens = 0
    if missing:
        vectors, new_tokens = embed(missing)
        recorded_at = date.today().isoformat()
        for text, vector in zip(missing, vectors, strict=True):
            known[text_key(text)] = [round(x, DECIMALS) for x in vector]
    write({key: known[key] for key in wanted}, tokens + new_tokens, recorded_at)
    cost = new_tokens * USD_PER_MILLION_TOKENS / 1_000_000
    print(
        f"{len(missing)} vectores nuevos ({new_tokens} tokens, ~{cost:.6f} USD), "
        f"{len(stale)} obsoletos eliminados, {len(wanted)} en total."
    )


if __name__ == "__main__":
    main()
