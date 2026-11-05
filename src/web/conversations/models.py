import uuid

from django.db import models
from django.db.models import Q

from web.conversations.links import link_expiry
from web.tenancy.models import TenantScopedModel


class Offer(TenantScopedModel):
    """Stable identity; descriptive configuration belongs to immutable profile versions."""

    key = models.CharField(max_length=60)

    class Meta:
        constraints = (models.UniqueConstraint(fields=["tenant", "key"], name="uniq_offer_key"),)


class Application(TenantScopedModel):
    offer = models.ForeignKey(Offer, null=True, on_delete=models.PROTECT)
    erased_at = models.DateTimeField(null=True)
    is_test = models.BooleanField(default=False)
    # Synthetic data safety and its presentation are independent: manual walkthroughs
    # use the normal workbench; simulator runs stay in the FDE simulation section.
    is_simulation = models.BooleanField(default=False)
    segment = models.CharField(max_length=60, blank=True, default="")
    external_id = models.CharField(max_length=60)
    candidate_name = models.CharField(max_length=200)
    phone = models.CharField(max_length=30)
    city = models.CharField(max_length=120)
    job_ref = models.CharField(max_length=60)
    timezone = models.CharField(max_length=40, default="Europe/Madrid")

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["tenant", "external_id"], name="uniq_application_external"
            ),
        )

    def __str__(self) -> str:
        return self.external_id


class CandidateLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(default=link_expiry)
    revoked_at = models.DateTimeField(null=True)
    tenant = models.ForeignKey("tenancy.Tenant", on_delete=models.CASCADE)
    application_id = models.UUIDField()

    def __str__(self) -> str:
        return str(self.id)


class TelephoneAttempt(TenantScopedModel):
    """A consented, budget-reserved call. An uncertain dispatch is never retried."""

    session = models.ForeignKey("Session", on_delete=models.PROTECT)
    profile_version = models.ForeignKey("profiles.ProfileVersion", on_delete=models.PROTECT)
    request_id = models.UUIDField()
    link_digest = models.CharField(max_length=64)
    contact = models.CharField(max_length=30)
    contact_digest = models.CharField(max_length=64)
    consent_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    reserved_usd_micros = models.PositiveIntegerField(default=2_000_000)
    state = models.CharField(max_length=20, default="queued")
    reason = models.CharField(max_length=100, blank=True)
    attempted_at = models.DateTimeField(null=True)
    provider_id = models.CharField(max_length=34, blank=True)
    stream_id = models.CharField(max_length=34, blank=True)
    stream_used_at = models.DateTimeField(null=True)
    duration_seconds = models.PositiveIntegerField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "request_id"], name="uniq_phone_request"),
            models.UniqueConstraint(
                fields=["provider_id"], condition=~Q(provider_id=""), name="uniq_phone_provider"
            ),
        )


class SmsNotification(TenantScopedModel):
    """Consent and durable single-send reservation; bearer tokens are never stored here."""

    session = models.ForeignKey("Session", on_delete=models.PROTECT)
    link = models.ForeignKey(CandidateLink, on_delete=models.PROTECT)
    key = models.CharField(max_length=160)
    purpose = models.CharField(max_length=20)
    contact = models.CharField(max_length=30)
    consent_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    segment_count = models.PositiveIntegerField()
    reserved_usd_micros = models.PositiveIntegerField()
    state = models.CharField(max_length=20, default="queued")
    reason = models.CharField(max_length=100, blank=True)
    attempted_at = models.DateTimeField(null=True)
    provider_id = models.CharField(max_length=34, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "key"], name="uniq_sms_key"),
            models.UniqueConstraint(
                fields=["provider_id"], condition=~Q(provider_id=""), name="uniq_sms_provider"
            ),
        )


class ChannelInbound(TenantScopedModel):
    provider_id = models.CharField(max_length=200)
    payload = models.JSONField(default=dict)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "provider_id"], name="uniq_inbound_provider"),
        )


class ChannelDelivery(TenantScopedModel):
    provider_id = models.CharField(max_length=200)
    status = models.CharField(max_length=12)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "provider_id"], name="uniq_delivery_signal"),
        )


class Session(TenantScopedModel):
    human_owner = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    application = models.ForeignKey(Application, on_delete=models.PROTECT)
    profile_version = models.ForeignKey("profiles.ProfileVersion", on_delete=models.PROTECT)
    channel = models.CharField(
        max_length=10,
        choices=[("web", "Web"), ("sim", "Simulación"), ("whatsapp", "WhatsApp"), ("voice", "Voz")],
    )
    status = models.CharField(
        max_length=20,
        choices=[(s, s) for s in ("active", "handed_off", "completed", "abandoned")],
        default="active",
    )
    run_context = models.JSONField(default=dict)
    system_prompt = models.TextField()
    ended_at = models.DateTimeField(null=True)
    # Last language observed in the candidate's messages; "" until one is classified.
    language = models.CharField(max_length=8, blank=True, default="")
    preferred_language = models.CharField(max_length=2, blank=True, default="")
    fallback_language = models.CharField(max_length=2, blank=True, default="")
    # Result field (or "summary"/"interview") still pending when the session was abandoned.
    abandoned_stage = models.CharField(max_length=60, blank=True, default="")

    def __str__(self) -> str:
        return f"{self.id}:{self.status}"


class Message(TenantScopedModel):
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    direction = models.CharField(max_length=3, choices=[("in", "Entrada"), ("out", "Salida")])
    text = models.TextField()
    client_message_id = models.CharField(max_length=60, blank=True)
    channel = models.CharField(max_length=10, default="web")
    provider_message_id = models.CharField(max_length=200, blank=True)
    delivery_status = models.CharField(max_length=12, blank=True)
    simulated = models.BooleanField(default=False)
    language = models.CharField(max_length=8, blank=True, default="")
    # Observation for tone only ({"mood", "quote", "tone"}); never a hiring criterion.
    signal = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["session", "client_message_id"],
                condition=~Q(client_message_id=""),
                name="uniq_session_client_message",
            ),
            models.UniqueConstraint(
                fields=["tenant", "channel", "provider_message_id"],
                condition=~Q(provider_message_id=""),
                name="uniq_channel_provider_message",
            ),
        )

    def __str__(self) -> str:
        return f"{self.session_id}:{self.direction}"


class ChannelIdentity(TenantScopedModel):
    provider = models.CharField(max_length=12, default="meta")
    onboarding = models.JSONField(default=dict)
    channel = models.CharField(max_length=10)
    contact = models.CharField(max_length=200)
    application = models.ForeignKey(Application, on_delete=models.PROTECT, null=True)
    opted_out = models.BooleanField(default=False)
    last_inbound_at = models.DateTimeField(null=True)
    simulated = models.BooleanField(default=False)
    clock_offset_seconds = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["tenant", "channel", "contact"], name="uniq_channel_contact"
            ),
        )

    def __str__(self) -> str:
        return f"{self.channel}:{self.id}"


class TwilioOutbound(TenantScopedModel):
    """Durable single-attempt transport; an unknown POST is never retried."""

    identity = models.ForeignKey(ChannelIdentity, on_delete=models.PROTECT)
    message = models.OneToOneField(Message, null=True, on_delete=models.PROTECT)
    text = models.TextField()
    key = models.CharField(max_length=200)
    state = models.CharField(max_length=16, default="pending")
    provider_id = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=100, blank=True)
    attempted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "key"], name="uniq_twilio_outbound"),
        )


class Followup(TenantScopedModel):
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    contact = models.CharField(max_length=200)
    run_at = models.DateTimeField()
    reason = models.CharField(max_length=300)
    status = models.CharField(max_length=20, default="proposed")
    simulated = models.BooleanField(default=False)
    last_inbound_at = models.DateTimeField(null=True)
    job_id = models.UUIDField(null=True)
    idempotency_key = models.CharField(max_length=64)
    author = models.CharField(max_length=20, default="conversador")
    # "web" reminders stay inside the candidate's own conversation; contact is then empty.
    channel = models.CharField(max_length=10, default="whatsapp")

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["tenant", "idempotency_key"], name="uniq_followup_key"),
        )

    def __str__(self) -> str:
        return f"{self.id}:{self.status}"


class ModelMessage(TenantScopedModel):
    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    seq = models.PositiveIntegerField()
    role = models.CharField(
        max_length=10, choices=[(s, s) for s in ("user", "assistant", "tool", "system")]
    )
    content = models.JSONField(default=dict)

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=["session", "seq"], name="uniq_session_model_seq"),
        )

    def __str__(self) -> str:
        return f"{self.session_id}:{self.seq}"


class Event(TenantScopedModel):
    session = models.ForeignKey(Session, on_delete=models.PROTECT, null=True)
    type = models.CharField(max_length=60)
    payload = models.JSONField(default=dict)

    def __str__(self) -> str:
        return f"{self.id}:{self.type}"


class InterviewBooking(TenantScopedModel):
    """Platform record of one interview; the calendar connector owns the slot itself."""

    session = models.ForeignKey(Session, on_delete=models.PROTECT)
    application = models.ForeignKey(Application, on_delete=models.PROTECT)
    connection_id = models.CharField(max_length=40)
    slot_ref = models.CharField(max_length=60)
    site_ref = models.CharField(max_length=60, blank=True, default="")
    site_name = models.CharField(max_length=200)
    site_address = models.CharField(max_length=300, blank=True, default="")
    site_timezone = models.CharField(max_length=40)
    candidate_timezone = models.CharField(max_length=40)
    starts_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=15)
    status = models.CharField(
        max_length=20,
        choices=[(s, s) for s in ("booked", "rescheduled", "cancelled")],
        default="booked",
    )
    # iCalendar SEQUENCE: +1 on every reschedule or cancellation of the same UID.
    sequence = models.PositiveIntegerField(default=0)
    cancelled_by = models.CharField(max_length=20, blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    reminder_at = models.DateTimeField(null=True, blank=True)
    reminder_status = models.CharField(max_length=20, blank=True, default="")
    reminder_job_id = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=["tenant", "application"],
                condition=Q(status__in=["booked", "rescheduled"]),
                name="uniq_active_interview",
            ),
        )
        indexes = (models.Index(fields=["tenant", "starts_at"], name="interview_starts_idx"),)

    def __str__(self) -> str:
        return f"{self.id}:{self.status}"
