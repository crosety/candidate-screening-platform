import pytest
from jsonschema import Draft202012Validator

from tests.api.test_sms_links import phone, setup, sms
from web.api import api

__all__ = ["phone", "setup", "sms"]
pytestmark = pytest.mark.django_db(transaction=True)


def assert_contract(path, method, response):
    assert response.status_code == 200
    spec = api.get_openapi_schema()
    schema = spec["paths"][path][method]["responses"][200]["content"]["application/json"]["schema"]
    assert schema
    Draft202012Validator({"allOf": [schema], "components": spec["components"]}).validate(
        response.json()
    )


def test_telephone_status_and_request_have_usable_response_contracts(setup):
    s = setup
    body = {"session_id": str(s.sid), "token": s.token}
    path = "/api/v1/chat/telephone/status"
    assert_contract(path, "post", s.client.post(path, body, content_type="application/json"))
    body.update(
        request_id="00000000-0000-4000-8000-000000000001", phone="+34600000000", consent=True
    )
    path = "/api/v1/chat/telephone/request"
    assert_contract(path, "post", s.client.post(path, body, content_type="application/json"))
    path = "/api/v1/chat/telephone/status"
    response = s.client.post(path, body, content_type="application/json")
    assert response.json()["attempt"]["state"] == "queued"
    assert_contract(path, "post", response)


def test_all_candidate_sms_actions_and_recruiter_reads_follow_the_contract(sms):
    s = sms
    body = dict(
        session_id=str(s.sid),
        token=s.token,
        request_id="00000000-0000-4000-8000-000000000002",
        phone="+34600000000",
        consent=True,
    )
    path = "/api/v1/chat/sms-link"
    for action in ("status", "send", "status", "revoke", "status"):
        response = s.client.post(path, {**body, "action": action}, content_type="application/json")
        assert_contract(path, "post", response)
    path = "/api/v1/tenants/{tenant_id}/sessions/{session_id}/sms-link"
    response = s.client.get(path.format(tenant_id=s.tenant, session_id=s.sid))
    assert response.json()["notifications"][0]["revoked"] is True
    assert_contract(path, "get", response)


def test_recruiter_sms_request_follows_the_contract(sms):
    path = "/api/v1/tenants/{tenant_id}/sessions/{session_id}/sms-link"
    response = sms.client.post(
        path.format(tenant_id=sms.tenant, session_id=sms.sid),
        {
            "request_id": "00000000-0000-4000-8000-000000000003",
            "phone": "+34600000000",
            "consent": True,
        },
        content_type="application/json",
    )
    assert response.json()["state"] == "queued"
    assert_contract(path, "post", response)
