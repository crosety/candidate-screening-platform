"""Exercise the launcher as a CLI; no provider or real credential is involved."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def run_launcher(
    root: Path, extra_env: dict[str, str], *command: str
) -> subprocess.CompletedProcess[str]:
    (root / "tools").mkdir(exist_ok=True)
    shutil.copyfile("tools/runtime.py", root / "tools/runtime.py")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OPENAI_", "ASM_", "AWS_", "LLM_"))
    }
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(root / "tools/runtime.py"), *command],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_runtime_accepts_an_existing_private_key_file_without_overwriting_it(
    tmp_path: Path,
) -> None:
    secret = tmp_path / "existing-key"
    secret.write_text("synthetic-runtime-test-only")
    result = run_launcher(
        tmp_path,
        {"LLM_MODE": "openai", "OPENAI_API_KEY_FILE": str(secret)},
        sys.executable,
        "-c",
        "import os; from pathlib import Path; "
        "assert Path(os.environ['OPENAI_API_KEY_FILE']).read_text() == "
        "'synthetic-runtime-test-only'; print('started')",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "started"
    assert secret.read_text() == "synthetic-runtime-test-only"
    assert "synthetic-runtime-test-only" not in result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["fake", "openai"])
def test_compose_gets_only_the_configured_file_reference(tmp_path: Path, mode: str) -> None:
    secret = tmp_path / "existing-key"
    secret.write_text("synthetic-runtime-test-only")
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import os\nfrom pathlib import Path\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "assert Path(os.environ['OPENAI_API_KEY_FILE']).read_text() == "
        "'synthetic-runtime-test-only'\nprint('compose-started')\n"
    )
    docker.chmod(0o700)
    result = run_launcher(
        tmp_path,
        {
            "LLM_MODE": mode,
            "OPENAI_API_KEY_FILE": str(secret),
            "OPENAI_API_KEY": "unused-synthetic-value",
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
        },
        "docker",
        "compose",
        "up",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "compose-started"
    assert secret.read_text() == "synthetic-runtime-test-only"


@pytest.mark.parametrize(
    "key", ["", "{{resolve:secretsmanager:synthetic:key}}", "unused-synthetic-value"]
)
def test_offline_materialization_preserves_a_previously_provisioned_key(
    tmp_path: Path, key: str
) -> None:
    folder = tmp_path / ".runtime-secrets"
    folder.mkdir()
    secret = folder / "openai_api_key"
    secret.write_text("synthetic-runtime-test-only")
    result = run_launcher(
        tmp_path,
        {"LLM_MODE": "fake", "OPENAI_API_KEY": key},
        "--materialize",
        sys.executable,
        "-c",
        "print('offline-started')",
    )
    assert result.returncode == 0, result.stderr
    assert secret.read_text() == "synthetic-runtime-test-only"
    assert secret.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_invalid_key_files_fail_without_running_the_command(
    tmp_path: Path, content: str | None
) -> None:
    secret = tmp_path / "missing-or-empty-key"
    if content is not None:
        secret.write_text(content)
    result = run_launcher(
        tmp_path,
        {"LLM_MODE": "openai", "OPENAI_API_KEY_FILE": str(secret)},
        sys.executable,
        "-c",
        "print('must-not-run')",
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
