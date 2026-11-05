from django.db import models
from django.db.backends.base.base import BaseDatabaseWrapper


class VectorTextField(models.TextField):
    """Django state/flush integration; the repository owns parametrized vector writes."""

    def db_type(self, connection: BaseDatabaseWrapper) -> str:
        return "vector(1536)"
