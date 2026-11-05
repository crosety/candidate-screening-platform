"""Attribute paid calls to the current maintenance job, never concurrent candidates."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class UsageCounter:
    micros: int = 0


current_usage: ContextVar[UsageCounter | None] = ContextVar("maintenance_usage", default=None)
