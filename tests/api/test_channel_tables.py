import pytest
from django.db import IntegrityError

from web.conversations.models import ChannelIdentity
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


def test_contacts_and_opt_out_are_tenant_scoped() -> None:
    a = Tenant.objects.create(slug="phone_a", name="A")
    b = Tenant.objects.create(slug="phone_b", name="B")
    with tenant_context(a.id):
        row = ChannelIdentity.objects.create(
            tenant=a, channel="whatsapp", contact="synthetic", opted_out=True
        )
    assert ChannelIdentity.objects.count() == 0
    with tenant_context(b.id):
        assert not ChannelIdentity.objects.filter(pk=row.id).exists()
        ChannelIdentity.objects.create(tenant=b, channel="whatsapp", contact="synthetic")
    with tenant_context(a.id):
        assert ChannelIdentity.objects.get(pk=row.id).opted_out
    with pytest.raises(IntegrityError), tenant_context(a.id):
        ChannelIdentity.objects.create(tenant=a, channel="whatsapp", contact="synthetic")
