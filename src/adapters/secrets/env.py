import os
import re
from pathlib import Path

from screening.domain.errors import NotFound


class EnvSecretStore:
    def resolve(self, ref: str) -> str:
        match = re.fullmatch(r"secret://([a-z0-9_-]+)/([a-z0-9_-]+)", ref)
        if match is None:
            raise NotFound("Referencia de secreto no válida")
        tenant, name = match.groups()
        key = f"SECRET_{tenant}_{name}".upper().replace("-", "_")
        value = os.environ.get(key)
        filename = os.environ.get(f"{key}_FILE")
        if value is not None and filename is not None:
            raise NotFound("Configuración de secreto ambigua")
        if filename:
            # Only trusted server configuration supplies paths; refs cannot select files.
            try:
                path = Path(filename)
                if not path.is_absolute() or not path.is_file() or path.stat().st_size > 65_536:
                    raise ValueError
                value = path.read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                raise NotFound("Secreto no disponible") from None
        if not value:
            raise NotFound("Secreto no encontrado")
        return value
