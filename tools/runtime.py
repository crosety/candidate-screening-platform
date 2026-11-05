"""Resolve runtime secret references without printing or persisting their values."""

import os
import shutil
import sys
from pathlib import Path

import environ


def main() -> None:
    environ.Env.read_env(Path(__file__).resolve().parents[1] / ".env", overwrite=False)
    command = sys.argv[1:]
    materialize = bool(command and command[0] == "--materialize")
    if materialize:
        command = command[1:]
    if not command:
        raise SystemExit("Falta el comando de arranque.")
    key_path = os.environ.get("OPENAI_API_KEY_FILE", "")
    if key_path:
        source = Path(key_path).expanduser().resolve()
        if not source.is_file() or not os.access(source, os.R_OK):
            raise SystemExit("OPENAI_API_KEY_FILE debe apuntar a un archivo privado legible.")
        if os.environ.get("LLM_MODE") == "openai" and not source.read_text().strip():
            raise SystemExit("El archivo de la clave está vacío.")
        os.environ["OPENAI_API_KEY_FILE"] = str(source)
        os.environ.pop("OPENAI_API_KEY", None)
    if os.environ.get("LLM_MODE") == "openai" and not key_path:
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise SystemExit("Falta OPENAI_API_KEY_FILE o una referencia en OPENAI_API_KEY.")
        if key.startswith("{{resolve:secretsmanager:"):
            wrapper = os.environ.get("ASM_EXEC_BIN") or shutil.which("asm-exec")
            if not wrapper or not Path(wrapper).is_file():
                raise SystemExit("Instala asm-exec o configura ASM_EXEC_BIN en .env.")
            target = (
                [sys.executable, __file__, "--materialize", *command]
                if command[:2] == ["docker", "compose"]
                else command
            )
            os.execv(sys.executable, [sys.executable, wrapper, "--", *target])
    if (materialize or command[:2] == ["docker", "compose"]) and not key_path:
        # Compose receives only a file path, never a resolved key in its environment.
        secret_dir = Path(__file__).resolve().parents[1] / ".runtime-secrets"
        secret_dir.mkdir(mode=0o700, exist_ok=True)
        secret_dir.chmod(0o700)
        secret_file = secret_dir / "openai_api_key"
        key = os.environ.pop("OPENAI_API_KEY", "")
        if os.environ.get("LLM_MODE") != "openai":
            # A leftover env reference/value is not a credential rotation in fake mode.
            key = ""
        # Offline startup must not destroy a key provisioned for a later live run.
        if key or not secret_file.exists():
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(key)
        secret_file.chmod(0o600)
        os.environ["OPENAI_API_KEY_FILE"] = str(secret_file)
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
