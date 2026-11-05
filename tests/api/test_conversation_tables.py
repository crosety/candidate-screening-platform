import pytest
from django.core.management import call_command
from django.db import DatabaseError

from web.conversations.models import Application, Event, ModelMessage, Session
from web.profiles.models import ProfileVersion
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("operation", ["update", "delete"])
@pytest.mark.parametrize("kind", ["event", "history"])
def test_conversation_history_is_append_only(operation: str, kind: str) -> None:
    call_command("seed_local")
    tenant = Tenant.objects.get(slug="grupo_sazon")
    with tenant_context(tenant.id):
        app = Application.objects.create(
            tenant=tenant, external_id="test", candidate_name="Demo", phone="", city="", job_ref=""
        )
        session = Session.objects.create(
            tenant=tenant,
            application=app,
            profile_version=ProfileVersion.objects.get(tenant=tenant),
            channel="web",
            system_prompt="fixed",
        )
        if kind == "event":
            obj = Event.objects.create(tenant=tenant, session=session, type="session_opened")
        else:
            obj = ModelMessage.objects.create(
                tenant=tenant, session=session, seq=0, role="user", content={}
            )
    with pytest.raises(DatabaseError), tenant_context(tenant.id):
        query = type(obj).objects.filter(pk=obj.id)
        if operation == "update":
            query.update(created_at=obj.created_at)
        else:
            query.delete()
