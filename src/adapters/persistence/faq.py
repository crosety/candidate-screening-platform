import asyncio
import json
import math
from uuid import UUID, uuid4

from asgiref.sync import async_to_sync, sync_to_async
from django.db import connection

from screening.application.faq import FaqService, Fragment, fragments
from screening.application.ports import VersionRecord
from screening.domain.errors import NotAllowed
from screening.domain.run_context import RunContext
from web.profiles.models import ProfileVersion
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def prepare_faq(service: FaqService, tenant_id: UUID, version: VersionRecord) -> None:
    """Finish indexing before activation; failure rolls back the publication transaction."""
    if not fragments(version.content):
        return
    with tenant_context(tenant_id):
        slug = Tenant.objects.get(pk=tenant_id).slug
    ctx = RunContext(
        "copiloto", tenant_id, slug, "tenant", "fde", None, None, version.id, frozenset(), {}
    )

    async def run() -> None:
        async with asyncio.timeout(35):
            await service.index(ctx, version.content)

    try:
        async_to_sync(run)()
    except Exception as exc:
        raise NotAllowed(
            "No se ha activado la versión: el índice de FAQ no está listo. "
            "Reintenta la publicación."
        ) from exc


def vector_text(vector: list[float]) -> str:
    if len(vector) != 1536 or not all(math.isfinite(v) for v in vector):
        raise ValueError("Embedding no válido.")
    return json.dumps(vector)


class PgvectorFaqStore:
    @sync_to_async
    def replace(
        self,
        tenant_id: UUID,
        version_id: UUID,
        fragments: list[Fragment],
        vectors: list[list[float]],
        model_id: str,
    ) -> None:
        if len(fragments) != len(vectors):
            raise ValueError("Faltan embeddings.")
        with tenant_context(tenant_id), connection.cursor() as cursor:
            if not ProfileVersion.objects.filter(pk=version_id, tenant_id=tenant_id).exists():
                raise ValueError("Versión no encontrada.")
            cursor.execute(
                "DELETE FROM profiles_faqchunk WHERE tenant_id=%s AND version_id=%s",
                [tenant_id, version_id],
            )
            for item, vector in zip(fragments, vectors, strict=True):
                cursor.execute(
                    "INSERT INTO profiles_faqchunk "
                    "(id,created_at,tenant_id,version_id,source_id,source_name,"
                    "offset_pos,text,embedding,model_id) "
                    "VALUES (%s,NOW(),%s,%s,%s,%s,%s,%s,%s::vector,%s)",
                    [
                        uuid4(),
                        tenant_id,
                        version_id,
                        item.source_id,
                        item.source_name,
                        item.offset,
                        item.text,
                        vector_text(vector),
                        model_id,
                    ],
                )

    @sync_to_async
    def search(
        self,
        tenant_id: UUID,
        version_id: UUID,
        vector: list[float],
        model_id: str,
        source_ids: list[str] | None = None,
    ) -> list[dict[str, str | float]]:
        with tenant_context(tenant_id), connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_id,source_name,text,1-(embedding <=> %s::vector) AS score "
                "FROM profiles_faqchunk WHERE tenant_id=%s AND version_id=%s AND model_id=%s "
                "AND (%s::text[] IS NULL OR source_id = ANY(%s::text[])) "
                "ORDER BY embedding <=> %s::vector LIMIT 3",
                [
                    vector_text(vector),
                    tenant_id,
                    version_id,
                    model_id,
                    source_ids,
                    source_ids,
                    vector_text(vector),
                ],
            )
            return [
                {"source_id": row[0], "source_name": row[1], "text": row[2], "score": row[3]}
                for row in cursor.fetchall()
            ]
