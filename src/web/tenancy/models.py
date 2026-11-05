import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    slug = models.CharField(
        max_length=40, unique=True, validators=[RegexValidator(r"^[a-z0-9_]+$")]
    )
    name = models.CharField(max_length=200)
    client_approval_required = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class Membership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    allowed_segments = models.JSONField(default=list)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=20, choices=[("admin", "Admin"), ("fde", "FDE"), ("recruiter", "Reclutador")]
    )

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["user", "tenant"], name="unique_membership"),
        )

    def __str__(self) -> str:
        return f"{self.user_id}:{self.tenant_id}:{self.role}"


class TenantScopedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, db_index=True)

    class Meta:
        abstract = True


class AuditEvent(TenantScopedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)

    def __str__(self) -> str:
        return f"{self.action}:{self.id}"
