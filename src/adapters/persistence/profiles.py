import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from uuid import UUID

from django.db.models import Max
from django.utils import timezone

from screening.application.ports import DraftRecord, PublicationRecord, VersionRecord
from screening.domain.errors import Conflict, NotAllowed, NotFound
from screening.domain.profile import ProfileContent
from web.profiles.models import Approval, ProfileDraft, ProfileVersion, Publication, SegmentPilot
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def _draft(row: ProfileDraft) -> DraftRecord:
    return DraftRecord(
        row.tenant_id, row.rev, ProfileContent.model_validate(row.content), row.base_version_id
    )


def _version(row: ProfileVersion) -> VersionRecord:
    return VersionRecord(
        row.id,
        row.tenant_id,
        row.seq,
        row.version,
        ProfileContent.model_validate(row.content),
        row.content_hash,
        row.risk,
        row.note,
        row.published_by_id,
        row.created_at.astimezone(UTC),
    )


def _publication(row: Publication) -> PublicationRecord:
    return PublicationRecord(
        row.id,
        row.tenant_id,
        row.version_id,
        row.previous_version_id,
        row.percent,
        row.state,
        row.needs_second_approval,
        row.needs_client_approval,
        list(
            Approval.objects.filter(publication=row).order_by("kind").values_list("kind", flat=True)
        ),
        row.created_by_id,
        row.created_at.astimezone(UTC),
        row.segment,
    )


class DjangoProfileRepository:
    @contextmanager
    def locked(self, tenant_id: UUID) -> Iterator[None]:
        with tenant_context(tenant_id):
            Tenant.objects.select_for_update().get(pk=tenant_id)
            yield

    def list_publications(self, tenant_id: UUID) -> list[PublicationRecord]:
        with tenant_context(tenant_id):
            return [
                _publication(row)
                for row in Publication.objects.filter(tenant_id=tenant_id).order_by("-created_at")
            ]

    def was_activated(self, tenant_id: UUID, version_id: UUID) -> bool:
        with tenant_context(tenant_id):
            return Publication.objects.filter(
                tenant_id=tenant_id, version_id=version_id, activated_at__isnull=False
            ).exists()

    def cancel_publication(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord:
        with self.locked(tenant_id):
            record = self.get_publication(tenant_id, publication_id)
            if record.state != "pending_approval":
                raise NotAllowed("Esta publicación ya no está pendiente de aprobación.")
            Publication.objects.filter(pk=publication_id, tenant_id=tenant_id).update(
                state="cancelled"
            )
            return self.get_publication(tenant_id, publication_id)

    def get_draft(self, tenant_id: UUID) -> DraftRecord:
        with tenant_context(tenant_id):
            try:
                return _draft(ProfileDraft.objects.get(tenant_id=tenant_id))
            except ProfileDraft.DoesNotExist:
                raise NotFound("Borrador no encontrado") from None

    def save_draft(
        self, tenant_id: UUID, expected_rev: int, content: ProfileContent, user_id: UUID
    ) -> DraftRecord:
        with tenant_context(tenant_id):
            Tenant.objects.select_for_update().get(pk=tenant_id)
            try:
                row = ProfileDraft.objects.select_for_update().get(tenant_id=tenant_id)
            except ProfileDraft.DoesNotExist:
                raise NotFound("Borrador no encontrado") from None
            if row.rev != expected_rev:
                raise Conflict("El borrador ha cambiado; recarga.")
            row.content = content.model_dump(mode="json")
            row.rev += 1
            row.updated_by_id = user_id
            row.save(update_fields=["content", "rev", "updated_by", "updated_at"])
            return _draft(row)

    def replace_draft(
        self, tenant_id: UUID, content: ProfileContent, base_version_id: UUID | None, user_id: UUID
    ) -> DraftRecord:
        with tenant_context(tenant_id):
            Tenant.objects.select_for_update().get(pk=tenant_id)
            row = ProfileDraft.objects.filter(tenant_id=tenant_id).first()
            if row is None:
                row = ProfileDraft(tenant_id=tenant_id, rev=1)
            else:
                row.rev += 1
            row.content = content.model_dump(mode="json")
            row.base_version_id = base_version_id
            row.updated_by_id = user_id
            row.save()
            return _draft(row)

    def list_versions(self, tenant_id: UUID) -> list[VersionRecord]:
        with tenant_context(tenant_id):
            return [
                _version(v)
                for v in ProfileVersion.objects.filter(tenant_id=tenant_id).order_by("-seq")
            ]

    def get_version(self, tenant_id: UUID, version_id: UUID) -> VersionRecord:
        with tenant_context(tenant_id):
            try:
                return _version(ProfileVersion.objects.get(pk=version_id, tenant_id=tenant_id))
            except ProfileVersion.DoesNotExist:
                raise NotFound("Versión no encontrada") from None

    def create_version(
        self, tenant_id: UUID, content: ProfileContent, risk: str, note: str, user_id: UUID
    ) -> VersionRecord:
        with tenant_context(tenant_id):
            Tenant.objects.select_for_update().get(pk=tenant_id)
            seq = (
                ProfileVersion.objects.filter(tenant_id=tenant_id).aggregate(value=Max("seq"))[
                    "value"
                ]
                or 0
            ) + 1
            data = content.model_dump(mode="json")
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            row = ProfileVersion.objects.create(
                tenant_id=tenant_id,
                seq=seq,
                version=f"1.{seq - 1}",
                content=data,
                content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                schema_version=content.schema_version,
                risk=risk,
                note=note,
                published_by_id=user_id,
            )
            return _version(row)

    def active_publication(self, tenant_id: UUID) -> PublicationRecord | None:
        with tenant_context(tenant_id):
            row = (
                Publication.objects.filter(tenant_id=tenant_id, state="active", segment="")
                .order_by("-created_at")
                .first()
            )
            return _publication(row) if row else None

    def create_publication(
        self,
        tenant_id: UUID,
        version_id: UUID,
        previous_version_id: UUID | None,
        percent: int,
        needs_second: bool,
        needs_client: bool,
        note: str,
        user_id: UUID,
        segment: str = "",
    ) -> PublicationRecord:
        with tenant_context(tenant_id):
            self.get_version(tenant_id, version_id)
            if previous_version_id is not None:
                self.get_version(tenant_id, previous_version_id)
            row = Publication.objects.create(
                tenant_id=tenant_id,
                version_id=version_id,
                previous_version_id=previous_version_id,
                percent=percent,
                state="pending_approval",
                needs_second_approval=needs_second,
                needs_client_approval=needs_client,
                note=note,
                created_by_id=user_id,
                segment=segment,
            )
            return _publication(row)

    def get_publication(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord:
        with tenant_context(tenant_id):
            try:
                return _publication(Publication.objects.get(pk=publication_id, tenant_id=tenant_id))
            except Publication.DoesNotExist:
                raise NotFound("Publicación no encontrada") from None

    def add_approval(
        self, tenant_id: UUID, publication_id: UUID, kind: str, user_id: UUID
    ) -> PublicationRecord:
        with tenant_context(tenant_id):
            try:
                publication = Publication.objects.select_for_update().get(
                    pk=publication_id, tenant_id=tenant_id
                )
            except Publication.DoesNotExist:
                raise NotFound("Publicación no encontrada") from None
            Approval.objects.get_or_create(
                tenant_id=tenant_id,
                publication=publication,
                kind=kind,
                defaults={"approved_by_id": user_id},
            )
            return _publication(publication)

    def activate(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord:
        with tenant_context(tenant_id):
            Tenant.objects.select_for_update().get(pk=tenant_id)
            try:
                row = Publication.objects.get(pk=publication_id, tenant_id=tenant_id)
            except Publication.DoesNotExist:
                raise NotFound("Publicación no encontrada") from None
            if row.state != "pending_approval":
                raise NotAllowed("Solo puede activarse una publicación pendiente.")
            current = self.active_publication(tenant_id)
            if row.previous_version_id != (current.version_id if current else None):
                raise Conflict("La versión activa ha cambiado; vuelve a preparar la publicación.")
            approvals = set(Approval.objects.filter(publication=row).values_list("kind", flat=True))
            if (row.needs_second_approval and "second_fde" not in approvals) or (
                row.needs_client_approval and "client" not in approvals
            ):
                raise NotAllowed("Faltan aprobaciones para activar esta publicación.")
            pending = Publication.objects.filter(tenant_id=tenant_id, state="pending_approval")
            active = Publication.objects.filter(tenant_id=tenant_id, state="active")
            if row.segment:
                if current is None:
                    raise NotAllowed("Un piloto necesita una versión general activa.")
                pending = pending.filter(segment=row.segment)
                active = active.filter(segment=row.segment)
                SegmentPilot.objects.update_or_create(
                    tenant_id=tenant_id,
                    segment=row.segment,
                    defaults={
                        "version_id": row.version_id,
                        "baseline_id": current.version_id,
                        "percent": row.percent,
                        "enabled": True,
                        "created_by_id": row.created_by_id,
                    },
                )
            else:
                SegmentPilot.objects.filter(tenant_id=tenant_id, enabled=True).update(enabled=False)
            pending.exclude(pk=row.id).update(state="cancelled")
            active.exclude(pk=row.id).update(state="superseded")
            row.state = "active"
            row.activated_at = timezone.now()
            row.save(update_fields=["state", "activated_at"])
            return _publication(row)

    def tenant_flags(self, tenant_id: UUID) -> tuple[str, bool]:
        with tenant_context(tenant_id):
            try:
                row = Tenant.objects.get(pk=tenant_id)
            except Tenant.DoesNotExist:
                raise NotFound("Cliente no encontrado") from None
            return row.name, row.client_approval_required
