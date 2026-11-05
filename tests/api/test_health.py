import pytest
from django.test import Client


@pytest.mark.django_db
def test_health(client: Client) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "redis": "ok"}
