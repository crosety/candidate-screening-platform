from typing import Literal
from uuid import UUID

from asgiref.sync import async_to_sync
from django.http import HttpRequest
from ninja import Router, Schema

from screening.application.ports import Suggestion
from screening.platform.container import get_container
from web.tenancy.permissions import require_role

router = Router()


class SuggestionIn(Schema):
    section: Literal["brief", "operating_model"]


class SuggestionOut(Schema):
    field: str
    why: str
    append: str


@router.post("/tenants/{tenant_id}/copilot/suggestions", response=list[SuggestionOut])
def suggestions(request: HttpRequest, tenant_id: UUID, payload: SuggestionIn) -> list[Suggestion]:
    require_role(request, tenant_id, {"admin", "fde"})
    return async_to_sync(get_container().copilot.suggestions)(tenant_id, payload.section)
