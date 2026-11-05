from typing import Literal
from uuid import UUID

from ninja import Schema


class LoginIn(Schema):
    email: str
    password: str


class MembershipOut(Schema):
    tenant_id: UUID
    tenant_slug: str
    tenant_name: str
    role: Literal["admin", "fde", "recruiter"]


class MeOut(Schema):
    id: UUID
    email: str
    full_name: str
    memberships: list[MembershipOut]
