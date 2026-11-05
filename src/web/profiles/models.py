from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from web.profiles.fields import VectorTextField
from web.tenancy.models import TenantScopedModel


class ProfileDraft(TenantScopedModel):
    tenant = models.OneToOneField("tenancy.Tenant", on_delete=models.CASCADE)
    content = models.JSONField(default=dict)
    rev = models.PositiveIntegerField(default=1)
    base_version = models.ForeignKey("ProfileVersion", on_delete=models.PROTECT, null=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.tenant_id}:rev{self.rev}"


class ProfileVersion(TenantScopedModel):
    seq = models.PositiveIntegerField()
    version = models.CharField(max_length=10)
    content = models.JSONField(default=dict)
    content_hash = models.CharField(max_length=64)
    schema_version = models.PositiveIntegerField()
    risk = models.CharField(max_length=10)
    note = models.CharField(max_length=300, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "seq"], name="unique_profile_seq"),
        )

    def __str__(self) -> str:
        return f"{self.tenant_id}:{self.version}"


class Publication(TenantScopedModel):
    segment = models.CharField(max_length=60, blank=True)
    version = models.ForeignKey(
        ProfileVersion, on_delete=models.PROTECT, related_name="publications"
    )
    previous_version = models.ForeignKey(
        ProfileVersion, on_delete=models.PROTECT, null=True, related_name="+"
    )
    percent = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(100)])
    state = models.CharField(
        max_length=20,
        choices=[(s, s) for s in ("pending_approval", "active", "superseded", "cancelled")],
        default="pending_approval",
    )
    needs_second_approval = models.BooleanField(default=False)
    needs_client_approval = models.BooleanField(default=False)
    note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    activated_at = models.DateTimeField(null=True)

    def __str__(self) -> str:
        return f"{self.version_id}:{self.state}"


class Approval(TenantScopedModel):
    publication = models.ForeignKey(Publication, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=[("second_fde", "FDE"), ("client", "Cliente")])
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["publication", "kind"], name="unique_approval_kind"),
        )

    def __str__(self) -> str:
        return f"{self.publication_id}:{self.kind}"


class ConnectorStatus(TenantScopedModel):
    connection_id = models.CharField(max_length=40)
    summary = models.CharField(max_length=200)
    warnings = models.JSONField(default=list)
    checked_at = models.DateTimeField()

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["tenant", "connection_id"], name="unique_connector_status"
            ),
        )


class ImprovementProposal(TenantScopedModel):
    base_rev = models.PositiveIntegerField()
    content = models.JSONField()
    diff = models.JSONField(default=dict)
    evidence = models.JSONField(default=list)
    rationale = models.TextField()
    state = models.CharField(max_length=20, default="proposed")
    eval_run = models.ForeignKey("evals.EvalRun", null=True, on_delete=models.PROTECT)
    accepted_rev = models.PositiveIntegerField(null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class MaintenancePolicy(TenantScopedModel):
    tenant = models.OneToOneField("tenancy.Tenant", on_delete=models.CASCADE)
    nightly_enabled = models.BooleanField(default=False)
    improvement_enabled = models.BooleanField(default=False)
    timezone = models.CharField(max_length=80, default="Europe/Madrid")
    nightly_hour = models.PositiveSmallIntegerField(default=3)
    judge_percent = models.PositiveSmallIntegerField(default=0)
    daily_budget_micros = models.PositiveIntegerField(default=300000)
    run_budget_micros = models.PositiveIntegerField(default=100000)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class BudgetReservation(TenantScopedModel):
    key = models.CharField(max_length=150)
    day = models.DateField()
    kind = models.CharField(max_length=20)
    reserved_micros = models.PositiveIntegerField()
    actual_micros = models.PositiveIntegerField(default=0)
    state = models.CharField(max_length=20, default="reserved")
    job_id = models.UUIDField(null=True)
    session_id = models.UUIDField(null=True)

    class Meta:
        constraints = (models.UniqueConstraint(fields=["tenant", "key"], name="uniq_budget_key"),)


class SegmentPilot(TenantScopedModel):
    segment = models.CharField(max_length=60)
    version = models.ForeignKey(ProfileVersion, on_delete=models.PROTECT, related_name="pilots")
    baseline = models.ForeignKey(
        ProfileVersion, on_delete=models.PROTECT, related_name="pilot_baselines"
    )
    percent = models.PositiveSmallIntegerField(default=10)
    enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "segment"], name="uniq_pilot_segment"),
        )


class FaqChunk(TenantScopedModel):
    version = models.ForeignKey(ProfileVersion, on_delete=models.CASCADE)
    source_id = models.CharField(max_length=40)
    source_name = models.CharField(max_length=120)
    offset_pos = models.PositiveIntegerField()
    text = models.TextField()
    model_id = models.CharField(max_length=80)
    embedding = VectorTextField()

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["tenant", "version", "source_id", "offset_pos"], name="unique_faq_fragment"
            ),
        )
