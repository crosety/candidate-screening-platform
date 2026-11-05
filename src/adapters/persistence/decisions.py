from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import IntegrityError
from django.utils import timezone

from screening.application.ports import DecisionRecord, OutboxRecord, ResultRecord
from screening.domain.errors import Conflict, NotFound
from web.conversations.models import Session
from web.decisions.models import Decision, OutboxItem, Result
from web.tenancy.context import tenant_context


def _decision(row: Decision) -> DecisionRecord:
    return DecisionRecord(
        row.id,
        row.tenant_id,
        row.session_id,
        row.decision_type,
        row.status,
        row.autonomy_level,
        row.decided_by,
        row.user_id,
        row.reason,
        row.created_at,
        row.applied_at,
    )


def _outbox(row: OutboxItem) -> OutboxRecord:
    return OutboxRecord(
        row.id,
        row.tenant_id,
        row.session_id,
        row.kind,
        deepcopy(row.payload),
        row.status,
        row.attempts,
    )


class DjangoDecisionRepository:
    @sync_to_async
    def get_result(self, tenant_id: UUID, session_id: UUID) -> ResultRecord | None:
        with tenant_context(tenant_id):
            row = Result.objects.filter(tenant_id=tenant_id, session_id=session_id).first()
            if row is None:
                return None
            fields = deepcopy(row.fields)
            reason = str(fields.pop("__reason", ""))
            return ResultRecord(
                session_id,
                row.verdict,
                fields,
                row.flags,
                row.summary,
                reason,
                row.evaluator_model,
                row.sentiment,
            )

    @sync_to_async
    def save_result(self, tenant_id: UUID, result: ResultRecord) -> None:
        try:
            with tenant_context(tenant_id):
                if not Session.objects.filter(tenant_id=tenant_id, pk=result.session_id).exists():
                    raise NotFound("Conversación no encontrada")
                Result.objects.create(
                    tenant_id=tenant_id,
                    session_id=result.session_id,
                    verdict=result.verdict,
                    fields={**deepcopy(result.fields), "__reason": result.reason},
                    flags=result.flags,
                    summary=result.summary,
                    evaluator_model=result.evaluator_model,
                    sentiment=result.sentiment,
                )
        except IntegrityError:
            raise Conflict("La conversación ya tiene un resultado.") from None

    @sync_to_async
    def create_decision(
        self,
        tenant_id: UUID,
        session_id: UUID,
        decision_type: str,
        status: str,
        autonomy_level: int,
        decided_by: str,
        user_id: UUID | None,
        reason: str,
        applied_at: datetime | None,
    ) -> DecisionRecord:
        with tenant_context(tenant_id):
            if not Session.objects.filter(tenant_id=tenant_id, pk=session_id).exists():
                raise NotFound("Conversación no encontrada")
            return _decision(
                Decision.objects.create(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    decision_type=decision_type,
                    status=status,
                    autonomy_level=autonomy_level,
                    decided_by=decided_by,
                    user_id=user_id,
                    reason=reason,
                    applied_at=applied_at,
                )
            )

    @sync_to_async
    def get_decision(self, tenant_id: UUID, decision_id: UUID) -> DecisionRecord:
        with tenant_context(tenant_id):
            try:
                return _decision(Decision.objects.get(tenant_id=tenant_id, pk=decision_id))
            except Decision.DoesNotExist:
                raise NotFound("Decisión no encontrada") from None

    @sync_to_async
    def set_decision_status(
        self,
        tenant_id: UUID,
        decision_id: UUID,
        status: str,
        user_id: UUID | None,
        applied_at: datetime | None,
    ) -> DecisionRecord:
        with tenant_context(tenant_id):
            try:
                row = Decision.objects.select_for_update().get(tenant_id=tenant_id, pk=decision_id)
            except Decision.DoesNotExist:
                raise NotFound("Decisión no encontrada") from None
            if row.status != "proposed":
                raise Conflict("Esta decisión ya no está pendiente.")
            row.status, row.user_id, row.applied_at = status, user_id, applied_at
            if user_id is not None:
                row.decided_by = "user"
            row.save(update_fields=["status", "user", "applied_at", "decided_by"])
            return _decision(row)

    @sync_to_async
    def list_proposed(self, tenant_id: UUID) -> list[DecisionRecord]:
        with tenant_context(tenant_id):
            return [
                _decision(row)
                for row in Decision.objects.filter(tenant_id=tenant_id, status="proposed").order_by(
                    "created_at", "id"
                )
            ]

    @sync_to_async
    def has_applied_qualify(self, tenant_id: UUID, session_id: UUID) -> bool:
        with tenant_context(tenant_id):
            return Decision.objects.filter(
                tenant_id=tenant_id,
                session_id=session_id,
                decision_type="qualify",
                status="applied",
            ).exists()

    @sync_to_async
    def create_outbox(
        self,
        tenant_id: UUID,
        session_id: UUID,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboxRecord:
        with tenant_context(tenant_id):
            if not Session.objects.filter(tenant_id=tenant_id, pk=session_id).exists():
                raise NotFound("Conversación no encontrada")
            row, _ = OutboxItem.objects.get_or_create(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                defaults={"session_id": session_id, "kind": kind, "payload": deepcopy(payload)},
            )
            return _outbox(row)

    @sync_to_async
    def get_outbox(self, tenant_id: UUID, item_id: UUID) -> OutboxRecord:
        with tenant_context(tenant_id):
            try:
                return _outbox(OutboxItem.objects.get(tenant_id=tenant_id, pk=item_id))
            except OutboxItem.DoesNotExist:
                raise NotFound("Efecto no encontrado") from None

    @sync_to_async
    def mark_outbox(self, tenant_id: UUID, item_id: UUID, status: str, error: str = "") -> None:
        with tenant_context(tenant_id):
            try:
                row = OutboxItem.objects.select_for_update().get(tenant_id=tenant_id, pk=item_id)
            except OutboxItem.DoesNotExist:
                raise NotFound("Efecto no encontrado") from None
            row.status, row.last_error = status, error
            row.attempts += 1
            if status == "sent":
                row.sent_at = timezone.now()
            row.save(update_fields=["status", "last_error", "attempts", "sent_at"])
