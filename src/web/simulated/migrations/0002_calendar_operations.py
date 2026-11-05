import uuid

import django.db.models.deletion
from django.db import migrations, models

from web.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [
        ("simulated", "0001_initial"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="site",
            name="address",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.CreateModel(
            name="CalendarOperation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("idempotency_key", models.CharField(max_length=64)),
                ("operation", models.CharField(max_length=30)),
                ("result", models.JSONField(default=dict)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="tenancy.tenant"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("tenant", "idempotency_key"), name="uniq_calendar_operation"
                    )
                ],
            },
        ),
        enable_rls("simulated_calendaroperation"),
    ]
