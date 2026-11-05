import json
import re
from pathlib import Path

from screening.domain.evals import Persona


class FilePersonaSource:
    def __init__(self, seeds_dir: Path) -> None:
        self.seeds_dir = seeds_dir

    def list_personas(self, tenant_slug: str, offer_key: str = "") -> list[Persona]:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", tenant_slug) or (
            offer_key and not re.fullmatch(r"[a-zA-Z0-9_-]+", offer_key)
        ):
            raise ValueError("Referencia de escenarios no válida")
        directory = self.seeds_dir / "tenants" / tenant_slug
        specific = directory / "offers" / offer_key / "personas.json"
        path = specific if offer_key and specific.is_file() else directory / "personas.json"
        return (
            [Persona.model_validate(item) for item in json.loads(path.read_text())]
            if path.exists()
            else []
        )
