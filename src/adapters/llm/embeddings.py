import hashlib
import math
from time import perf_counter

from openai import AsyncOpenAI

from screening.application.ports import LlmUsage, TraceSink
from screening.domain.run_context import RunContext


class OpenAIEmbeddings:
    model_id = "text-embedding-3-small"
    dimensions = 1536

    def __init__(self, sink: TraceSink):
        self.sink = sink

    async def embed(self, ctx: RunContext, texts: list[str]) -> list[list[float]]:
        started = perf_counter()
        async with AsyncOpenAI(timeout=30, max_retries=1) as client:
            response = await client.embeddings.create(
                model=self.model_id,
                input=texts,
                dimensions=self.dimensions,
                encoding_format="float",
            )
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            raise ValueError("Dimensión o número de embeddings no válido.")
        tokens = response.usage.prompt_tokens
        await self.sink.record_llm_call(
            LlmUsage(
                ctx.tenant_id,
                ctx.session_id,
                ctx.eval_case_id,
                "embeddings",
                self.model_id,
                "",
                tokens,
                0,
                0,
                0,
                math.ceil(tokens * 0.02),
                round((perf_counter() - started) * 1000),
                "complete",
            )
        )
        return vectors


class FakeEmbeddings:
    model_id = "fake-embeddings-1536"
    dimensions = 1536

    async def embed(self, ctx: RunContext, texts: list[str]) -> list[list[float]]:
        # Test-only deterministic vectors; not represented as semantic embeddings.
        return [
            [float(byte) / 255 for byte in hashlib.sha256(t.encode()).digest()] * 48 for t in texts
        ]
