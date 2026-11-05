import json
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from web.api import api


class Command(BaseCommand):
    help = "Exporta el contrato OpenAPI para generar el cliente TypeScript."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--output", required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["output"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as output:
            json.dump(api.get_openapi_schema(), output, indent=2, ensure_ascii=False)
            output.write("\n")
