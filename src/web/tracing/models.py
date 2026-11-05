from django.db import models

from web.tenancy.models import TenantScopedModel


class LlmCall(TenantScopedModel):
    eval_case = models.ForeignKey("evals.EvalCase", on_delete=models.PROTECT, null=True)
    session = models.ForeignKey("conversations.Session", on_delete=models.PROTECT, null=True)
    agent = models.CharField(max_length=20)
    model_id = models.CharField(max_length=80)
    effort = models.CharField(max_length=10)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    cache_write_tokens = models.PositiveIntegerField(default=0)
    cost_usd_micros = models.BigIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    stop_reason = models.CharField(max_length=40, blank=True)

    def __str__(self) -> str:
        return f"{self.agent}:{self.id}"


class VoiceMeasurement(TenantScopedModel):
    """Metrics-only delivery snapshot; created once and removed by privacy redaction."""

    session = models.ForeignKey("conversations.Session", on_delete=models.PROTECT)
    conversation_id = models.CharField(max_length=100)
    project_id = models.CharField(max_length=100)
    spans = models.JSONField(default=list)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["tenant", "conversation_id", "project_id"],
                name="unique_voice_measurement",
            ),
        )


class CapabilityCall(TenantScopedModel):
    eval_case = models.ForeignKey("evals.EvalCase", on_delete=models.PROTECT, null=True)
    session = models.ForeignKey("conversations.Session", on_delete=models.PROTECT, null=True)
    capability = models.CharField(max_length=60)
    connection_id = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=[(s, s) for s in ("ok", "error", "blocked")])
    latency_ms = models.PositiveIntegerField(default=0)
    request = models.JSONField(default=dict)
    response = models.JSONField(default=dict)

    def __str__(self) -> str:
        return f"{self.capability}:{self.status}"
