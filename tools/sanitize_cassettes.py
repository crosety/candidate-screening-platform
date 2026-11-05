"""Remove transport cookies and account metadata from synthetic test recordings."""

import sys
from pathlib import Path

import yaml


def main() -> None:
    root = Path(__file__).resolve().parents[1] / "tests/cassettes"
    excluded = {
        "authorization",
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
        "openai-organization",
        "openai-project",
    }
    for name in sys.argv[1:]:
        path = Path(name).resolve()
        if not path.is_relative_to(root) or path.suffix != ".yaml":
            raise SystemExit("Solo se admiten casetes YAML dentro de tests/cassettes.")
        data = yaml.safe_load(path.read_text())
        for interaction in data["interactions"]:
            for side in ("request", "response"):
                interaction[side]["headers"] = {
                    key: value
                    for key, value in interaction[side]["headers"].items()
                    if key.lower() not in excluded
                }
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        print(f"Sanitizado: {path.name}; {len(data['interactions'])} interacciones.")


if __name__ == "__main__":
    main()
