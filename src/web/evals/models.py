from django.conf import settings
from django.db import models

from web.tenancy.models import TenantScopedModel


class EvalRun(TenantScopedModel):
    alternative_content = models.JSONField(null=True)
    draft_rev = models.PositiveIntegerField()
    persona_keys = models.JSONField(default=list)
    compare_published = models.BooleanField(default=True)
    last_error = models.TextField(blank=True)
    published_version = models.ForeignKey(
        "profiles.ProfileVersion", on_delete=models.PROTECT, null=True
    )
    status = models.CharField(max_length=10, default="queued")
    max_cost_usd_micros = models.BigIntegerField()
    score_draft = models.FloatField(null=True)
    score_published = models.FloatField(null=True)
    summary = models.JSONField(default=dict)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    finished_at = models.DateTimeField(null=True)


class EvalCase(TenantScopedModel):
    run = models.ForeignKey(EvalRun, on_delete=models.CASCADE)
    persona_key = models.CharField(max_length=60)
    target = models.CharField(max_length=10)
    transcript = models.JSONField(default=list)
    judge = models.JSONField(default=dict)
    score = models.FloatField(null=True)
    cost_usd_micros = models.BigIntegerField(default=0)
    status = models.CharField(max_length=10, default="pending")
