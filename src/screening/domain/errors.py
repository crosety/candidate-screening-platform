class DomainError(Exception):
    """Base error of the screening core."""


class NotAllowed(DomainError):
    pass


class Conflict(DomainError):
    pass


class NotFound(DomainError):
    pass


class EvaluationFailed(DomainError):
    pass
