"""Identity of the test harness, excluding credentials and mutable output data."""

import functools
import hashlib
from pathlib import Path

# Code that shapes what the agents say and decide in an eval. Consoles, APIs, workers and
# persistence do not, so editing them must not invalidate an approved suite.
RUNTIME = (
    "src/screening",
    "src/adapters/agent_langchain",
    "src/adapters/llm",
    "src/adapters/connectors_registry.py",
    "src/connectors",
    "src/web/simulated/connectors.py",
    "uv.lock",
)


def runtime_files(root: Path) -> list[Path]:
    paths: list[Path] = []
    for item in RUNTIME:
        path = root / item
        paths.extend(
            sorted(path.rglob("*.py")) if path.is_dir() else [path] if path.exists() else []
        )
    return paths


@functools.cache
def _runtime_digest() -> str:
    # Cached per process: a code change only takes effect after a restart anyway.
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in runtime_files(root):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def harness_metadata(mode: str) -> dict[str, str]:
    return {"schema": "review-v2", "runtime_sha256": _runtime_digest(), "mode": mode}
