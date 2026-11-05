from screening.domain.errors import NotFound


class DictSecretStore:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str:
        try:
            return self._values[ref]
        except KeyError:
            raise NotFound("Secreto no encontrado") from None
