import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock
from uuid import UUID, uuid4

from screening.application.ports import DraftRecord, PublicationRecord, VersionRecord
from screening.domain.errors import Conflict, NotAllowed, NotFound
from screening.domain.profile import ProfileContent


class InMemoryProfileRepository:
    def __init__(self) -> None:
        self.drafts: dict[UUID, DraftRecord] = {}
        self.versions: dict[UUID, VersionRecord] = {}
        self.publications: dict[UUID, PublicationRecord] = {}
        self.tenants: dict[UUID, tuple[str, bool]] = {}
        self._lock = RLock()
        self.activated: set[UUID] = set()

    @contextmanager
    def locked(self, tenant_id: UUID) -> Iterator[None]:
        with self._lock:
            yield

    def list_publications(self, tenant_id: UUID) -> list[PublicationRecord]:
        return deepcopy(
            sorted(
                (p for p in self.publications.values() if p.tenant_id == tenant_id),
                key=lambda p: p.created_at,
                reverse=True,
            )
        )

    def was_activated(self, tenant_id: UUID, version_id: UUID) -> bool:
        return version_id in self.activated and self.get_version(tenant_id, version_id) is not None

    def cancel_publication(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord:
        row = self.get_publication(tenant_id, publication_id)
        if row.state != "pending_approval":
            raise NotAllowed("Esta publicación ya no está pendiente de aprobación.")
        self.publications[row.id] = replace(row, state="cancelled")
        return self.get_publication(tenant_id, publication_id)

    def add_tenant(self, tenant_id: UUID, name: str, client_approval_required: bool = True) -> None:
        self.tenants[tenant_id] = (name, client_approval_required)

    def get_draft(self, tenant_id: UUID) -> DraftRecord:
        try:
            return deepcopy(self.drafts[tenant_id])
        except KeyError:
            raise NotFound("Borrador no encontrado") from None

    def save_draft(
        self, tenant_id: UUID, expected_rev: int, content: ProfileContent, user_id: UUID
    ) -> DraftRecord:
        draft = self.get_draft(tenant_id)
        if draft.rev != expected_rev:
            raise Conflict("El borrador ha cambiado; recarga.")
        self.drafts[tenant_id] = replace(
            draft, content=content.model_copy(deep=True), rev=draft.rev + 1
        )
        return self.get_draft(tenant_id)

    def replace_draft(
        self, tenant_id: UUID, content: ProfileContent, base_version_id: UUID | None, user_id: UUID
    ) -> DraftRecord:
        current = self.drafts.get(tenant_id)
        self.drafts[tenant_id] = DraftRecord(
            tenant_id,
            current.rev + 1 if current else 1,
            content.model_copy(deep=True),
            base_version_id,
        )
        return self.get_draft(tenant_id)

    def list_versions(self, tenant_id: UUID) -> list[VersionRecord]:
        return deepcopy(
            sorted(
                (v for v in self.versions.values() if v.tenant_id == tenant_id),
                key=lambda v: v.seq,
                reverse=True,
            )
        )

    def get_version(self, tenant_id: UUID, version_id: UUID) -> VersionRecord:
        version = self.versions.get(version_id)
        if version is None or version.tenant_id != tenant_id:
            raise NotFound("Versión no encontrada")
        return deepcopy(version)

    def create_version(
        self, tenant_id: UUID, content: ProfileContent, risk: str, note: str, user_id: UUID
    ) -> VersionRecord:
        seq = len(self.list_versions(tenant_id)) + 1
        canonical = json.dumps(
            content.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        version = VersionRecord(
            uuid4(),
            tenant_id,
            seq,
            f"1.{seq - 1}",
            content.model_copy(deep=True),
            hashlib.sha256(canonical.encode()).hexdigest(),
            risk,
            note,
            user_id,
            datetime.now(UTC),
        )
        self.versions[version.id] = version
        return deepcopy(version)

    def active_publication(self, tenant_id: UUID) -> PublicationRecord | None:
        rows = [
            p
            for p in self.publications.values()
            if p.tenant_id == tenant_id and p.state == "active" and not p.segment
        ]
        return deepcopy(max(rows, key=lambda p: p.created_at)) if rows else None

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
        self.get_version(tenant_id, version_id)
        if previous_version_id is not None:
            self.get_version(tenant_id, previous_version_id)
        publication = PublicationRecord(
            uuid4(),
            tenant_id,
            version_id,
            previous_version_id,
            percent,
            "pending_approval",
            needs_second,
            needs_client,
            [],
            user_id,
            datetime.now(UTC),
            segment,
        )
        self.publications[publication.id] = publication
        return deepcopy(publication)

    def get_publication(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord:
        publication = self.publications.get(publication_id)
        if publication is None or publication.tenant_id != tenant_id:
            raise NotFound("Publicación no encontrada")
        return deepcopy(publication)

    def add_approval(
        self, tenant_id: UUID, publication_id: UUID, kind: str, user_id: UUID
    ) -> PublicationRecord:
        publication = self.get_publication(tenant_id, publication_id)
        self.publications[publication_id] = replace(
            publication, approvals=sorted({*publication.approvals, kind})
        )
        return self.get_publication(tenant_id, publication_id)

    def activate(self, tenant_id: UUID, publication_id: UUID) -> PublicationRecord:
        target = self.get_publication(tenant_id, publication_id)
        if target.state != "pending_approval":
            raise NotAllowed("Solo puede activarse una publicación pendiente.")
        current = self.active_publication(tenant_id)
        if target.previous_version_id != (current.version_id if current else None):
            raise Conflict("La versión activa ha cambiado; vuelve a preparar la publicación.")
        if (target.needs_second_approval and "second_fde" not in target.approvals) or (
            target.needs_client_approval and "client" not in target.approvals
        ):
            raise NotAllowed("Faltan aprobaciones para activar esta publicación.")
        for key, publication in list(self.publications.items()):
            if target.segment and publication.segment != target.segment:
                continue
            if publication.tenant_id == tenant_id and publication.state == "active":
                self.publications[key] = replace(publication, state="superseded")
            elif publication.tenant_id == tenant_id and publication.state == "pending_approval":
                self.publications[key] = replace(publication, state="cancelled")
        self.publications[publication_id] = replace(target, state="active")
        self.activated.add(target.version_id)
        return self.get_publication(tenant_id, publication_id)

    def tenant_flags(self, tenant_id: UUID) -> tuple[str, bool]:
        try:
            return self.tenants[tenant_id]
        except KeyError:
            raise NotFound("Cliente no encontrado") from None
