from uuid import UUID

from asgiref.sync import sync_to_async

from adapters.privacy import pseudonymize
from screening.application.ports import CopilotMaterial
from web.conversations.models import Message, Session
from web.decisions.models import Decision, Result
from web.evals.models import EvalCase, EvalRun
from web.tenancy.context import tenant_context


class DjangoCopilotMaterialReader:
    @sync_to_async
    def recent(self, tenant_id: UUID) -> CopilotMaterial:
        with tenant_context(tenant_id):
            sessions = []
            for row in (
                Session.objects.filter(tenant_id=tenant_id, status__in=["completed", "handed_off"])
                .select_related("application")
                .order_by("-created_at")[:10]
            ):
                sessions.append(
                    {
                        "id": str(row.id),
                        "status": row.status,
                        "messages": [
                            {
                                **m,
                                "text": pseudonymize(
                                    m["text"], row.application.candidate_name, row.application.phone
                                ),
                            }
                            for m in (
                                Message.objects.filter(tenant_id=tenant_id, session_id=row.id)
                                .order_by("created_at", "id")
                                .values("direction", "text")
                            )
                        ],
                        "verdict": Result.objects.filter(tenant_id=tenant_id, session_id=row.id)
                        .values_list("verdict", flat=True)
                        .first(),
                    }
                )
            corrections = list(
                Decision.objects.filter(tenant_id=tenant_id, status="overridden")
                .order_by("-created_at")
                .values("decision_type")
            )
            run = EvalRun.objects.filter(tenant_id=tenant_id).order_by("-created_at").first()
            cases = (
                list(
                    EvalCase.objects.filter(
                        tenant_id=tenant_id, run_id=run.id, score__lt=70
                    ).values("persona_key", "score", "judge")
                )
                if run
                else []
            )
            return CopilotMaterial(
                sessions, [dict(row) for row in corrections], [dict(row) for row in cases]
            )
