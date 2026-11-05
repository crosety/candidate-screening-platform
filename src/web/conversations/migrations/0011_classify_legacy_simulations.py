"""Classify only enumerated seed/rehearsal records, keeping a reversible audit."""

import json
from pathlib import Path

from django.db import migrations

# Frozen historical data, not tenant-specific application behavior.
KNOWN = json.loads(
    (Path(__file__).parent / "data" / "0011_simulations.json").read_text(encoding="utf-8")
)
EVENT = "legacy_simulations_classified_h14"


def classify(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    Application = apps.get_model("conversations", "Application")
    Event = apps.get_model("conversations", "Event")
    with schema_editor.connection.cursor() as cursor:
        for tenant in Tenant.objects.filter(slug__in=KNOWN):
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant.id)])
            changed = []
            for external_id, name in KNOWN[tenant.slug].items():
                rows = Application.objects.filter(
                    tenant=tenant,
                    external_id=external_id,
                    candidate_name=name,
                    is_test=False,
                    erased_at__isnull=True,
                )
                changed.extend(str(pk) for pk in rows.values_list("pk", flat=True))
                rows.update(is_test=True)
            if changed:
                Event.objects.create(
                    tenant=tenant,
                    type=EVENT,
                    payload={
                        "application_ids": changed,
                        "previous_is_test": False,
                        "source": "seeds/tenants/*/applications.json; docs/plan/ESTADO.md H6.5/H7",
                    },
                )
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


def unclassify(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    Application = apps.get_model("conversations", "Application")
    Event = apps.get_model("conversations", "Event")
    with schema_editor.connection.cursor() as cursor:
        for tenant in Tenant.objects.filter(slug__in=KNOWN):
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant.id)])
            for event in Event.objects.filter(tenant=tenant, type=EVENT):
                Application.objects.filter(
                    tenant=tenant, pk__in=event.payload["application_ids"]
                ).update(is_test=False)
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("conversations", "0010_offers")]
    operations = [migrations.RunPython(classify, unclassify)]
