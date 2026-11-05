import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = """from typing import Annotated, NotRequired, Required, TypedDict
from langchain.agents.middleware import AgentMiddleware
from web.tenancy.models import Tenant

class Data(TypedDict):
    optional: Annotated[NotRequired[int], "metadata"]
    required: Annotated[Required[str], "metadata"]

data: Data = {"required": "valid"}
tenant = Tenant(name="valid", slug="valid")
tenant.name = "still valid"
"""


@pytest.mark.parametrize("invalid", [False, True])
def test_plugin_preserves_required_keys_and_django_types(tmp_path: Path, invalid: bool) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        SOURCE + ('bad: Data = {"optional": 1}\ntenant.name = ["invalid"]\n' if invalid else "")
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(root / "pyproject.toml"),
            "--cache-dir",
            # Integration test: do not modify the repository's type-check cache.
            str(tmp_path / ".mypy_cache"),
            str(source),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "INTERNAL ERROR" not in result.stdout + result.stderr
    if invalid:
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'Missing key "required"' in result.stdout
        assert "Incompatible types in assignment" in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
