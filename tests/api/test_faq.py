from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.db import connection
from django.test import Client

from adapters.persistence.faq import PgvectorFaqStore
from screening.application.faq import Fragment
from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.accounts.models import User
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def test_real_pgvector_cosine_isolated_by_version_tenant_and_model(
    phone: tuple[Client, UUID, UUID],
) -> None:
    _, tenant, _ = phone
    version = get_container().profiles.active_version(tenant)
    assert version
    store = PgvectorFaqStore()
    vector = [1.0] + [0.0] * 1535
    orthogonal = [0.0, 1.0] + [0.0] * 1534
    async_to_sync(store.replace)(
        tenant,
        version.id,
        [
            Fragment("faq", "FAQ sintética", 0, "Uniforme proporcionado."),
            Fragment("faq", "FAQ sintética", 1000, "Otro tema."),
        ],
        [vector, orthogonal],
        "test",
    )
    found = async_to_sync(store.search)(tenant, version.id, vector, "test")
    assert found[0]["text"] == "Uniforme proporcionado." and found[0]["score"] == 1.0
    assert found[1]["score"] == 0.0
    assert async_to_sync(store.search)(tenant, version.id, vector, "test", []) == []
    assert async_to_sync(store.search)(tenant, version.id, vector, "test", ["other"]) == []
    assert async_to_sync(store.search)(tenant, version.id, vector, "test", ["faq"]) == found
    other = Tenant.objects.get(slug="logistica")
    assert async_to_sync(store.search)(other.id, version.id, vector, "test") == []
    assert async_to_sync(store.search)(tenant, version.id, vector, "another-model") == []
    with tenant_context(other.id), connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM profiles_faqchunk")
        assert cursor.fetchone()[0] == 0
    with pytest.raises(ValueError):
        async_to_sync(store.search)(tenant, version.id, [float("nan")] * 1536, "test")


def test_faq_api_never_exposes_fde_sources_to_recruiter(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, _ = phone
    assert client.get(f"/api/v1/tenants/{tenant}/faq").status_code == 200
    client.force_login(User.objects.get(email="reclutadora@orbio.local"))
    assert client.get(f"/api/v1/tenants/{tenant}/faq").status_code == 403


def test_pgvector_filters_offer_sources_before_top_three(phone):
    _, tenant, _ = phone
    version = get_container().profiles.active_version(tenant)
    assert version
    store = PgvectorFaqStore()
    query = [1.0] + [0.0] * 1535
    allowed = [0.9, 0.1] + [0.0] * 1534
    async_to_sync(store.replace)(
        tenant,
        version.id,
        [Fragment(f"other_{i}", "Otra oferta", 0, "Documento ajeno") for i in range(3)]
        + [Fragment("allowed", "Oferta actual", 0, "Documento permitido")],
        [query, query, query, allowed],
        "test",
    )
    found = async_to_sync(store.search)(tenant, version.id, query, "test", ["allowed"])
    assert [f["text"] for f in found] == ["Documento permitido"]
