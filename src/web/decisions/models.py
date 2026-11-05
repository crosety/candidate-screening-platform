from django.conf import settings
from django.db import models

from web.tenancy.models import TenantScopedModel


class Result(TenantScopedModel):
    sentiment = models.JSONField(default=list)
    session = models.OneToOneField("conversations.Session", on_delete=models.PROTECT)
    verdict = models.CharField(
        max_length=20,
        choices=[(value, value) for value in ("qualified", "not_qualified", "review", "pool")],
    )
    fields = models.JSONField(default=dict)
    flags = models.JSONField(default=list)
    summary = models.TextField()
    evaluator_model = models.CharField(max_length=80)

    def __str__(self) -> str:
        return f"{self.session_id}:{self.verdict}"


class Decision(TenantScopedModel):
    session = models.ForeignKey("conversations.Session", on_delete=models.PROTECT)
    decision_type = models.CharField(
        max_length=20, choices=[(value, value) for value in ("qualify", "reject", "review", "pool")]
    )
    status = models.CharField(
        max_length=20,
        choices=[(value, value) for value in ("proposed", "applied", "overridden", "cancelled")],
    )
    autonomy_level = models.PositiveSmallIntegerField()
    decided_by = models.CharField(max_length=10, choices=[("agent", "agent"), ("user", "user")])
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    reason = models.TextField(blank=True)
    applied_at = models.DateTimeField(null=True)

    def __str__(self) -> str:
        return f"{self.decision_type}:{self.status}"


class OutboxItem(TenantScopedModel):
    session = models.ForeignKey("conversations.Session", on_delete=models.PROTECT)
    kind = models.CharField(
        max_length=30, choices=[(value, value) for value in ("ats.write_result", "ats.set_stage")]
    )
    payload = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=200, unique=True)
    status = models.CharField(
        max_length=10,
        default="pending",
        choices=[(value, value) for value in ("pending", "sent", "dead")],
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True)

    def __str__(self) -> str:
        return f"{self.kind}:{self.status}"
