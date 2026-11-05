from django.http import HttpRequest, JsonResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError
from ninja.errors import ValidationError as NinjaValidationError
from ninja.security import django_auth
from pydantic import ValidationError

from screening.domain.errors import Conflict, DomainError, NotFound
from web.accounts.api import router as accounts_router
from web.conversations.api import router as conversations_router
from web.conversations.apply_api import router as apply_router
from web.conversations.followup_api import router as followup_router
from web.conversations.human_api import router as human_router
from web.conversations.interview_api import router as interview_router
from web.conversations.phone_api import router as phone_router
from web.conversations.recruiter_api import router as recruiter_router
from web.conversations.sms_api import router as sms_router
from web.conversations.telephone_api import router as telephone_router
from web.conversations.voice_api import router as voice_router
from web.conversations.workspace_api import router as workspace_router
from web.core.api import router as core_router
from web.decisions.api import router as decisions_router
from web.evals.api import router as evals_router
from web.profiles.api import router as profiles_router
from web.profiles.copilot_api import router as copilot_router
from web.profiles.faq_api import router as faq_router
from web.profiles.improvement_api import router as improvement_router
from web.profiles.live_api import router as live_router
from web.tenancy.api import router as tenancy_router

api = NinjaAPI(title="Screening Platform API", version="1", auth=django_auth, urls_namespace="api")
api.add_router("", core_router)
api.add_router("", accounts_router)
api.add_router("", profiles_router)
api.add_router("", live_router)
api.add_router("", improvement_router)
api.add_router("", faq_router)
api.add_router("", tenancy_router)
api.add_router("", conversations_router)
api.add_router("", apply_router)
api.add_router("", phone_router)
api.add_router("", telephone_router)
api.add_router("", sms_router)
api.add_router("", voice_router)
api.add_router("", recruiter_router)
api.add_router("", workspace_router)
api.add_router("", human_router)
api.add_router("", interview_router)
api.add_router("", followup_router)
api.add_router("", decisions_router)
api.add_router("", evals_router)
api.add_router("", copilot_router)


@api.exception_handler(DomainError)
def domain_error(request: HttpRequest, exc: DomainError) -> JsonResponse:
    status = 409 if isinstance(exc, Conflict) else 404 if isinstance(exc, NotFound) else 400
    return JsonResponse({"detail": str(exc)}, status=status)


@api.exception_handler(NinjaValidationError)
def input_error(request: HttpRequest, exc: NinjaValidationError) -> JsonResponse:
    detail = exc.errors[0].get("msg", "Entrada no válida") if exc.errors else "Entrada no válida"
    return JsonResponse({"detail": f"Entrada no válida: {detail}"}, status=400)


@api.exception_handler(ValidationError)
def validation_error(request: HttpRequest, exc: ValidationError) -> JsonResponse:
    return JsonResponse({"detail": f"Entrada no válida: {exc.errors()[0]['msg']}"}, status=400)


@api.exception_handler(AuthenticationError)
def auth_error(request: HttpRequest, exc: AuthenticationError) -> JsonResponse:
    return JsonResponse({"detail": "Debes iniciar sesión"}, status=401)
