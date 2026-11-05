from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from ninja import Schema


class DecisionIn(Schema):
    action: Literal["confirm", "qualify", "reject"]
    reason: str = ""


class DecisionOut(Schema):
    id: UUID
    tenant_id: UUID
    session_id: UUID
    decision_type: str
    status: str
    autonomy_level: int
    decided_by: str
    user_id: UUID | None
    reason: str
    created_at: datetime
    applied_at: datetime | None


class InboxOut(Schema):
    decision_id: UUID
    session_id: UUID
    candidate_name: str
    decision_type: str
    reason: str
    summary: str
    fields: dict[str, Any]
    flags: list[str]
    created_at: datetime
