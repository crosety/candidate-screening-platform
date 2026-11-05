from typing import NewType
from uuid import UUID

TenantId = NewType("TenantId", UUID)
SessionId = NewType("SessionId", UUID)
ApplicationId = NewType("ApplicationId", UUID)
UserId = NewType("UserId", UUID)
