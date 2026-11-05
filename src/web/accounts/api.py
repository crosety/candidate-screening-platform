import logging

from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from ninja import Router
from ninja.decorators import decorate_view
from ninja.errors import HttpError

from web.accounts.models import User
from web.accounts.schemas import LoginIn, MembershipOut, MeOut
from web.core.security import rate_limit
from web.tenancy.models import Membership

logger = logging.getLogger("security.authentication")

router = Router()


def user_details(user: User) -> MeOut:
    memberships = (
        Membership.objects.filter(user=user).select_related("tenant").order_by("tenant__name")
    )
    return MeOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        memberships=[
            MembershipOut.model_validate(
                {
                    "tenant_id": m.tenant_id,
                    "tenant_slug": m.tenant.slug,
                    "tenant_name": m.tenant.name,
                    "role": m.role,
                }
            )
            for m in memberships
        ],
    )


@router.get("/auth/csrf", auth=None, response=dict[str, str])
@decorate_view(ensure_csrf_cookie)
def csrf(request: HttpRequest) -> dict[str, str]:
    return {"csrf_token": get_token(request)}


@router.post("/auth/login", auth=None, response=MeOut)
@decorate_view(csrf_protect)
def login(request: HttpRequest, payload: LoginIn) -> MeOut:
    rate_limit(request, "login_ip", 30)
    rate_limit(request, "login_account", 8, payload.email.lower())
    user = authenticate(request, username=payload.email.lower(), password=payload.password)
    if user is None:
        logger.warning("login_failed")
        raise HttpError(401, "Email o contraseña incorrectos")
    django_login(request, user)
    logger.info("login_success user_id=%s", user.id)
    return user_details(user)


@router.post("/auth/logout", response={204: None})
def logout(request: HttpRequest) -> tuple[int, None]:
    django_logout(request)
    return 204, None


@router.get("/me", response=MeOut)
def me(request: HttpRequest) -> MeOut:
    if not isinstance(request.user, User):
        raise HttpError(401, "Debes iniciar sesión")
    return user_details(request.user)
