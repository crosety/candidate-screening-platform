from datetime import datetime
from typing import Literal
from uuid import UUID

from ninja import Schema
from pydantic import Field

from screening.domain.profile import ProfileContent


class DraftOut(Schema):
    rev: int
    content: ProfileContent
    base_version: UUID | None


class SaveDraftIn(Schema):
    rev: int = Field(ge=1)
    content: ProfileContent


class CheckOut(Schema):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str


class ChangeOut(Schema):
    area: str
    label: str
    before: str
    after: str
    risk: Literal["low", "medium", "high"]
    needs_client: bool
    sentences: list[tuple[str, str]] | None


class DiffOut(Schema):
    changes: list[ChangeOut]
    risk: Literal["none", "low", "medium", "high"]
    needs_second_approval: bool
    needs_client_approval: bool


class VersionOut(Schema):
    id: UUID
    version: str
    seq: int
    risk: str
    note: str
    published_by: str
    created_at: datetime
    active_percent: int | None
    can_rollback: bool = False


class VersionDetailOut(VersionOut):
    content: ProfileContent


class PublicationIn(Schema):
    segment: str = Field(default="", max_length=60)
    note: str = Field(default="", max_length=300)
    percent: int = Field(default=100, ge=1, le=100)


class PublicationOut(Schema):
    segment: str = ""
    id: UUID
    version: str
    state: str
    percent: int
    needs_second_approval: bool
    needs_client_approval: bool
    approvals: list[str]


class ApprovalIn(Schema):
    kind: Literal["second_fde", "client"]


class RollbackIn(Schema):
    note: str = Field(default="", max_length=300)
