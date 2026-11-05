from django.db import migrations, models


def classify(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    Application = apps.get_model("conversations", "Application")
    with schema_editor.connection.cursor() as cursor:
        for tenant_id in Tenant.objects.values_list("id", flat=True):
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])
            Application.objects.filter(tenant_id=tenant_id, is_test=True).update(is_simulation=True)
        cursor.execute("SELECT set_config('app.tenant_id', '', true)")


class Migration(migrations.Migration):
    dependencies = [("conversations", "0011_classify_legacy_simulations")]
    operations = [
        migrations.AddField(
            model_name="application", name="is_simulation", field=models.BooleanField(default=False)
        ),
        migrations.RunPython(classify, migrations.RunPython.noop),
    ]
