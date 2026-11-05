import uuid

import django.db.models.deletion
from django.db import migrations, models

from web.tenancy.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0016_turn_signals"),
        ("tenancy", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="InterviewBooking",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("connection_id", models.CharField(max_length=40)),
                ("slot_ref", models.CharField(max_length=60)),
                ("site_ref", models.CharField(blank=True, default="", max_length=60)),
                ("site_name", models.CharField(max_length=200)),
                ("site_address", models.CharField(blank=True, default="", max_length=300)),
                ("site_timezone", models.CharField(max_length=40)),
                ("candidate_timezone", models.CharField(max_length=40)),
                ("starts_at", models.DateTimeField()),
                ("duration_minutes", models.PositiveSmallIntegerField(default=15)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("booked", "booked"),
                            ("rescheduled", "rescheduled"),
                            ("cancelled", "cancelled"),
                        ],
                        default="booked",
                        max_length=20,
                    ),
                ),
                ("sequence", models.PositiveIntegerField(default=0)),
                ("cancelled_by", models.CharField(blank=True, default="", max_length=20)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("reminder_at", models.DateTimeField(blank=True, null=True)),
                ("reminder_status", models.CharField(blank=True, default="", max_length=20)),
                ("reminder_job_id", models.UUIDField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "application",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="conversations.application",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to="conversations.session"
                    ),
                ),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="tenancy.tenant"
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["tenant", "starts_at"], name="interview_starts_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("status__in", ["booked", "rescheduled"])),
                        fields=("tenant", "application"),
                        name="uniq_active_interview",
                    )
                ],
            },
        ),
        enable_rls("conversations_interviewbooking"),
    ]
