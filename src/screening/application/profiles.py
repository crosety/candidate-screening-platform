from collections.abc import Callable
from uuid import UUID

from screening.application.ports import (
    Clock,
    DraftRecord,
    ProfileRepository,
    PublicationRecord,
    VersionRecord,
)
from screening.domain.checks import Check, run_checks
from screening.domain.diff import ProfileDiff, diff_profiles
from screening.domain.errors import Conflict, NotAllowed, NotFound
from screening.domain.profile import ProfileContent


class ProfileService:
    def __init__(
        self,
        repo: ProfileRepository,
        clock: Clock,
        evals_gate: Callable[[UUID, int], bool] | None = None,
        live_warnings: Callable[[UUID], list[str]] | None = None,
        prepare_version: Callable[[UUID, VersionRecord], None] | None = None,
    ) -> None:
        self.repo = repo
        self.clock = clock
        self.evals_gate = evals_gate
        self.live_warnings = live_warnings
        self.prepare_version = prepare_version

    def get_draft(self, tenant_id: UUID) -> DraftRecord:
        return self.repo.get_draft(tenant_id)

    def active_version(self, tenant_id: UUID) -> VersionRecord | None:
        active = self.repo.active_publication(tenant_id)
        return self.repo.get_version(tenant_id, active.version_id) if active else None

    def save_draft(
        self, tenant_id: UUID, rev: int, content: ProfileContent, user_id: UUID
    ) -> DraftRecord:
        return self.repo.save_draft(tenant_id, rev, content, user_id)

    def reset_draft(self, tenant_id: UUID, user_id: UUID) -> DraftRecord:
        publication = self.repo.active_publication(tenant_id)
        if publication is None:
            raise NotFound("Este cliente no tiene ninguna versión publicada.")
        version = self.repo.get_version(tenant_id, publication.version_id)
        return self.repo.replace_draft(tenant_id, version.content, version.id, user_id)

    def checks(self, tenant_id: UUID) -> list[Check]:
        return run_checks(
            self.repo.get_draft(tenant_id).content,
            self.live_warnings(tenant_id) if self.live_warnings else None,
        )

    def diff(self, tenant_id: UUID) -> ProfileDiff:
        publication = self.repo.active_publication(tenant_id)
        before = (
            self.repo.get_version(tenant_id, publication.version_id).content
            if publication
            else None
        )
        _, client_required = self.repo.tenant_flags(tenant_id)
        return diff_profiles(before, self.repo.get_draft(tenant_id).content, client_required)

    def list_versions(self, tenant_id: UUID) -> list[VersionRecord]:
        return self.repo.list_versions(tenant_id)

    def get_version(self, tenant_id: UUID, version_id: UUID) -> VersionRecord:
        return self.repo.get_version(tenant_id, version_id)

    def publish(
        self, tenant_id: UUID, note: str, percent: int, user_id: UUID, segment: str = ""
    ) -> PublicationRecord:
        with self.repo.locked(tenant_id):
            return self._publish(tenant_id, note, percent, user_id, segment)

    def _publish(
        self, tenant_id: UUID, note: str, percent: int, user_id: UUID, segment: str = ""
    ) -> PublicationRecord:
        if not 1 <= percent <= 100:
            raise NotAllowed("El porcentaje debe estar entre 1 y 100.")
        if any(c.severity == "error" for c in self.checks(tenant_id)):
            raise NotAllowed("No se puede publicar: hay errores en la configuración.")
        diff = self.diff(tenant_id)
        if diff.risk == "none":
            raise NotAllowed("No hay cambios que publicar.")
        draft = self.repo.get_draft(tenant_id)
        if self.evals_gate and not self.evals_gate(tenant_id, draft.rev):
            raise NotAllowed("Faltan evals aprobadas para este borrador.")
        previous = self.repo.active_publication(tenant_id)
        if segment and (
            previous is None
            or segment not in {s for c in draft.content.connections for s in c.segments}
        ):
            raise NotAllowed(
                "El piloto necesita un segmento configurado y una versión general activa."
            )
        version = self.repo.create_version(tenant_id, draft.content, diff.risk, note, user_id)
        publication = self.repo.create_publication(
            tenant_id,
            version.id,
            previous.version_id if previous else None,
            percent,
            diff.needs_second_approval,
            diff.needs_client_approval,
            note,
            user_id,
            segment,
        )
        if not publication.needs_second_approval and not publication.needs_client_approval:
            publication = self._activate(tenant_id, publication)
        return publication

    def _activate(self, tenant_id: UUID, publication: PublicationRecord) -> PublicationRecord:
        version = self.repo.get_version(tenant_id, publication.version_id)
        if self.prepare_version:
            self.prepare_version(tenant_id, version)
        publication = self.repo.activate(tenant_id, publication.id)
        # An approval may arrive after another FDE has continued editing the draft.
        # Rebase those edits without replacing their content with the older snapshot.
        draft = self.repo.get_draft(tenant_id)
        self.repo.replace_draft(tenant_id, draft.content, version.id, publication.created_by_id)
        return publication

    def approve(
        self, tenant_id: UUID, publication_id: UUID, kind: str, user_id: UUID
    ) -> PublicationRecord:
        with self.repo.locked(tenant_id):
            return self._approve(tenant_id, publication_id, kind, user_id)

    def _approve(
        self, tenant_id: UUID, publication_id: UUID, kind: str, user_id: UUID
    ) -> PublicationRecord:
        publication = self.repo.get_publication(tenant_id, publication_id)
        if kind not in ("second_fde", "client"):
            raise NotAllowed("Tipo de aprobación no válido.")
        required = (
            publication.needs_second_approval
            if kind == "second_fde"
            else publication.needs_client_approval
        )
        if not required:
            raise NotAllowed("Esta aprobación no hace falta.")
        if kind == "second_fde" and user_id == publication.created_by_id:
            raise NotAllowed("La segunda aprobación tiene que darla otra persona.")
        if publication.state == "active":
            return publication
        if publication.state != "pending_approval":
            raise NotAllowed("Esta publicación ya no está pendiente de aprobación.")
        active = self.repo.active_publication(tenant_id)
        if publication.previous_version_id != (active.version_id if active else None):
            raise Conflict("La versión activa ha cambiado; vuelve a preparar la publicación.")
        publication = self.repo.add_approval(tenant_id, publication_id, kind, user_id)
        required_approvals = set()
        if publication.needs_second_approval:
            required_approvals.add("second_fde")
        if publication.needs_client_approval:
            required_approvals.add("client")
        if required_approvals <= set(publication.approvals):
            publication = self._activate(tenant_id, publication)
        return publication

    def rollback(
        self, tenant_id: UUID, version_id: UUID, note: str, user_id: UUID
    ) -> PublicationRecord:
        with self.repo.locked(tenant_id):
            return self._rollback(tenant_id, version_id, note, user_id)

    def _rollback(
        self, tenant_id: UUID, version_id: UUID, note: str, user_id: UUID
    ) -> PublicationRecord:
        target = self.repo.get_version(tenant_id, version_id)
        if not self.repo.was_activated(tenant_id, version_id):
            raise NotAllowed("Solo se puede volver a una versión que haya estado activa.")
        previous = self.repo.active_publication(tenant_id)
        version = self.repo.create_version(tenant_id, target.content, "low", note, user_id)
        publication = self.repo.create_publication(
            tenant_id,
            version.id,
            previous.version_id if previous else None,
            100,
            False,
            False,
            note,
            user_id,
        )
        publication = self._activate(tenant_id, publication)
        self.repo.replace_draft(tenant_id, target.content, version.id, user_id)
        return publication

    def version_for_new_session(self, tenant_id: UUID, session_id: UUID) -> VersionRecord:
        publication = self.repo.active_publication(tenant_id)
        if publication is None:
            raise NotFound("Este cliente no tiene ninguna versión publicada.")
        version_id = publication.version_id
        if (
            publication.percent < 100
            and publication.previous_version_id is not None
            and int(session_id.hex[:8], 16) % 100 >= publication.percent
        ):
            version_id = publication.previous_version_id
        return self.repo.get_version(tenant_id, version_id)
