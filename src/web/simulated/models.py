from django.db import models

from web.tenancy.models import TenantScopedModel


class Site(TenantScopedModel):
    dataset = models.CharField(max_length=40)
    external_id = models.CharField(max_length=60)
    name = models.CharField(max_length=200)
    city = models.CharField(max_length=120)
    country = models.CharField(max_length=2)
    timezone = models.CharField(max_length=40)
    lat = models.FloatField()
    lng = models.FloatField()
    vacancies = models.JSONField(default=dict)
    has_calendar = models.BooleanField(default=False)
    address = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "external_id"], name="uniq_site_external"),
        )

    def __str__(self) -> str:
        return self.name


class Place(TenantScopedModel):
    dataset = models.CharField(max_length=40)
    name = models.CharField(max_length=200)
    aliases = models.JSONField(default=list)
    city = models.CharField(max_length=120)
    lat = models.FloatField()
    lng = models.FloatField()

    def __str__(self) -> str:
        return f"{self.name} ({self.city})"


class CalendarSlot(TenantScopedModel):
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    starts_at = models.DateTimeField()
    booked_application_external_id = models.CharField(max_length=60, blank=True)

    def __str__(self) -> str:
        return f"{self.site_id}:{self.starts_at.isoformat()}"


class CalendarOperation(TenantScopedModel):
    """Idempotency ledger of the simulated calendar: a retried write replays its result."""

    idempotency_key = models.CharField(max_length=64)
    operation = models.CharField(max_length=30)
    result = models.JSONField(default=dict)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"], name="uniq_calendar_operation"
            ),
        )

    def __str__(self) -> str:
        return f"{self.operation}:{self.idempotency_key[:12]}"


class AtsRecord(TenantScopedModel):
    external_id = models.CharField(max_length=60)
    candidate_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30)
    city = models.CharField(max_length=120)
    job_ref = models.CharField(max_length=60)
    stage = models.CharField(max_length=40)
    fields = models.JSONField(default=dict)
    notes = models.JSONField(default=list)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "external_id"], name="uniq_ats_external"),
        )

    def __str__(self) -> str:
        return self.external_id
