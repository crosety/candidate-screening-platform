import pytest

from screening.domain.errors import Conflict, DomainError, NotAllowed, NotFound


@pytest.mark.parametrize("error", [DomainError, NotAllowed, Conflict, NotFound])
def test_domain_errors(error: type[DomainError]) -> None:
    assert issubclass(error, DomainError)
