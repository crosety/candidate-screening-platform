from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync

from screening.domain.errors import NotFound
from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.tenancy.context import tenant_context
from web.tracing.models import CapabilityCall

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def test_assessment_reads_only_own_successful_domain_evidence(phone):
    _, tenant, app = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, app)
    session = async_to_sync(service.open_session)(token)
    sid = session.session_id
    with tenant_context(tenant):
        for name, status in [
            ("openings.find", "ok"),
            ("credential.verify", "error"),
            ("ats.write_result", "ok"),
            ("platform.tool_guard.jev", "ok"),
        ]:
            CapabilityCall.objects.create(
                tenant_id=tenant,
                session_id=sid,
                capability=name,
                connection_id="synthetic",
                status=status,
                response={"verified": name},
            )
        CapabilityCall.objects.create(
            tenant_id=tenant,
            capability="openings.find",
            connection_id="synthetic",
            status="ok",
            response={"wrong_session": True},
        )
    result = async_to_sync(service.repo.tool_evidence)(tenant, sid)
    assert len(result) == 1 and result[0]["response"] == {"verified": "openings.find"}
    with pytest.raises(NotFound):
        async_to_sync(service.repo.tool_evidence)(uuid4(), sid)
