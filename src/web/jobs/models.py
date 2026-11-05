import uuid

from django.db import models
from django.utils import timezone


class Job(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    tenant = models.ForeignKey("tenancy.Tenant", null=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=50)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=10,
        default="queued",
        choices=[(s, s) for s in ("queued", "running", "done", "failed", "dead", "cancelled")],
    )
    run_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    locked_by = models.CharField(max_length=100, blank=True)
    locked_at = models.DateTimeField(null=True)
    last_error = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=200, unique=True, null=True, blank=True)

    class Meta:
        indexes = (models.Index(fields=["status", "run_at"]),)

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}:{self.status}"
