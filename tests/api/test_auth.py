import pytest
from django.test import Client

from web.accounts.models import User
from web.tenancy.models import Membership, Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def fde_user() -> User:
    user = User.objects.create_user("FDE@ORBIO.LOCAL", "test-only-password", full_name="FDE")
    tenant = Tenant.objects.create(slug="a", name="Cliente A")
    Membership.objects.create(user=user, tenant=tenant, role="fde")
    return user


def test_me_requires_session(client: Client) -> None:
    assert client.get("/api/v1/me").status_code == 401


def test_login(client: Client, fde_user: User) -> None:
    response = client.post(
        "/api/v1/auth/login",
        {"email": fde_user.email, "password": "test-only-password"},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "fde@orbio.local"
    assert data["memberships"][0]["tenant_name"] == "Cliente A"
    assert data["memberships"][0]["role"] == "fde"
    assert client.get("/api/v1/me").status_code == 200


def test_wrong_password(client: Client, fde_user: User) -> None:
    response = client.post(
        "/api/v1/auth/login",
        {"email": fde_user.email, "password": "wrong"},
        content_type="application/json",
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Email o contraseña incorrectos"


def test_logout(client: Client, fde_user: User) -> None:
    client.force_login(fde_user)
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/me").status_code == 401


def test_csrf_is_required_for_login(fde_user: User) -> None:
    client = Client(enforce_csrf_checks=True)
    payload = {"email": fde_user.email, "password": "test-only-password"}
    assert (
        client.post("/api/v1/auth/login", payload, content_type="application/json").status_code
        == 403
    )
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    assert "csrftoken" in client.cookies
    response = client.post(
        "/api/v1/auth/login", payload, content_type="application/json", HTTP_X_CSRFTOKEN=token
    )
    assert response.status_code == 200
