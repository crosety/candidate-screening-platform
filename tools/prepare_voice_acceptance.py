"""Prepare synthetic voice cases through the FDE API; never open a model socket."""

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import environ
import httpx

CASE_NAMES = ("normal", "injection", "handoff", "correction", "reconnect", "takeover")


def request(client: httpx.Client, method: str, route: str, **kwargs: Any) -> Any:
    response = client.request(method, route, **kwargs)
    if response.status_code != 200:
        # Neither request bodies nor exception URLs are safe: they may contain credentials.
        raise RuntimeError(f"La API ha rechazado la operación (HTTP {response.status_code}).")
    return response.json()


def prepare_cases(
    client: httpx.Client,
    tenant_id: UUID,
    offer: str,
    expected_version: UUID,
    purposes: list[str],
    output: Path,
) -> dict[str, dict[str, Any]]:
    if not purposes or len(set(purposes)) != len(purposes):
        raise ValueError("Indica casos distintos; no se crean duplicados automáticamente.")
    if any(purpose not in CASE_NAMES for purpose in purposes):
        raise ValueError("Caso de aceptación desconocido.")
    base = f"/api/v1/tenants/{tenant_id}"
    versions = request(client, "GET", base + "/versions")
    if not any(
        row["id"] == str(expected_version) and row["active_percent"] == 100 for row in versions
    ):
        raise ValueError("La versión esperada no está publicada al 100 %.")
    output.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
    # Reserve the receipt before a write. Never overwrite or retry a prior ambiguous batch.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    cases: dict[str, dict[str, Any]] = {}
    with os.fdopen(fd, "w") as stream:

        def checkpoint() -> None:
            stream.seek(0)
            json.dump(cases, stream, indent=2)
            stream.truncate()
            stream.flush()
            os.fsync(stream.fileno())

        checkpoint()
        for purpose in purposes:
            created = request(
                client,
                "POST",
                base + "/simulated/ats",
                json={
                    "candidate_name": "Elena Prueba",
                    "phone": "",
                    "city": "Valencia",
                    "job_ref": offer,
                },
            )
            token = urlparse(created["candidate_link"]).path.rsplit("/", 1)[-1]
            case = {
                "tenant_id": str(tenant_id),
                "external_id": created["external_id"],
                "offer_key": offer,
                "token": token,
                "ready": False,
            }
            cases[purpose] = case
            checkpoint()
            opened = request(client, "POST", "/api/v1/chat/sessions", json={"token": token})
            case["session_id"] = opened["session_id"]
            case["is_test"] = opened["is_test"]
            checkpoint()
            detail = request(client, "GET", base + f"/sessions/{opened['session_id']}")
            if (
                not opened["is_test"]
                or opened["status"] != "active"
                or detail["version"]["id"] != str(expected_version)
                or detail["application"]["external_id"] != created["external_id"]
                or detail["application"]["job_ref"] != offer
                or detail["messages"]
                or detail["llm_calls"]
            ):
                raise RuntimeError("El caso no cumple el aislamiento o la versión esperada.")
            case["version_id"] = detail["version"]["id"]
            case["ready"] = True
            checkpoint()
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_id", type=UUID)
    parser.add_argument("output", type=Path)
    parser.add_argument("cases", nargs="+", choices=CASE_NAMES)
    parser.add_argument("--offer", required=True)
    parser.add_argument("--expect-version", required=True, type=UUID)
    parser.add_argument("--api-base", default="http://localhost:8000")
    parser.add_argument("--email", default="fde@orbio.local")
    args = parser.parse_args()
    origin = urlparse(args.api_base)
    if (
        origin.scheme not in {"http", "https"}
        or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
        or origin.username
        or origin.password
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
    ):
        parser.error("La aceptación solo usa una API local sin credenciales en la URL.")
    if args.output.exists():
        parser.error("Ya existe el recibo: consúltalo, no repitas el lote.")
    environ.Env.read_env(Path(__file__).resolve().parents[1] / ".env", overwrite=False)
    password = os.environ.get("SEED_PASSWORD", "")
    if not password:
        parser.error("Falta SEED_PASSWORD en el entorno privado.")
    try:
        with httpx.Client(base_url=args.api_base, timeout=30, follow_redirects=False) as client:
            client.headers["X-CSRFToken"] = request(client, "GET", "/api/v1/auth/csrf")[
                "csrf_token"
            ]
            request(
                client,
                "POST",
                "/api/v1/auth/login",
                json={"email": args.email, "password": password},
            )
            # Login rotates the CSRF cookie.
            client.headers["X-CSRFToken"] = request(client, "GET", "/api/v1/auth/csrf")[
                "csrf_token"
            ]
            cases = prepare_cases(
                client, args.tenant_id, args.offer, args.expect_version, args.cases, args.output
            )
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as exc:
        raise SystemExit(
            f"Preparación detenida ({type(exc).__name__}). Revisa el recibo privado; "
            "no repitas escrituras si el resultado es incierto. No se ha abierto voz."
        ) from None
    print(json.dumps({name: case["session_id"] for name, case in cases.items()}))


if __name__ == "__main__":
    main()
