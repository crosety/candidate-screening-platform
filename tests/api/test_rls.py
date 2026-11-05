import pytest
from django.db import connection
from django.db.utils import InternalError, ProgrammingError

from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent, Tenant

pytestmark = pytest.mark.django_db(transaction=True)


def test_app_role_is_not_superuser() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert cursor.fetchone() == (False, False)


def test_tenant_tables_have_forced_rls() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            WHERE n.nspname = 'public' AND a.attname = 'tenant_id'
              AND a.attnum > 0 AND NOT a.attisdropped AND c.relkind = 'r'
            """
        )
        platform_tables = {"tenancy_membership", "jobs_job", "conversations_candidatelink"}
        rows = cursor.fetchall()
        assert rows
        for name, enabled, forced in rows:
            if name not in platform_tables:
                assert enabled and forced, name


def test_audit_events_are_isolated() -> None:
    a = Tenant.objects.create(slug="a", name="A")
    b = Tenant.objects.create(slug="b", name="B")
    with tenant_context(a.id):
        AuditEvent.objects.create(tenant=a, action="example")
    with tenant_context(b.id):
        assert AuditEvent.objects.count() == 0
    with tenant_context(a.id):
        assert AuditEvent.objects.count() == 1
    assert AuditEvent.objects.count() == 0


def test_cannot_write_other_tenant() -> None:
    a = Tenant.objects.create(slug="a", name="A")
    b = Tenant.objects.create(slug="b", name="B")
    with pytest.raises((ProgrammingError, InternalError)), tenant_context(b.id):
        AuditEvent.objects.create(tenant=a, action="invalid")
