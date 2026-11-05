"""Load test for the candidate chat path with the fake model, plus a queue benchmark.

The tool never targets the shared local stack. It refuses the main database and
Redis index, starts its own ASGI server with ``LLM_MODE=fake`` on a free port and
drives the same public path as the chat app: signed ingest, session opening and
the WebSocket turn loop. The fake runner excludes model and provider latency, so
results measure platform overhead only.

Usage (always through an isolation wrapper that sets DATABASE_URL/REDIS_URL):

    uv run python tools/loadtest.py prepare
    uv run python tools/loadtest.py chat --levels 1,10,20,40 --turns 8
    uv run python tools/loadtest.py queue --jobs 2000 --workers 1,2,4
    uv run python tools/loadtest.py capacity
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import platform
import secrets
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

DESCRIPTION = "Fake-model load test, queue benchmark and capacity model."
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / ".local" / "loadtest"
TENANT = "grupo_sazon"
OFFER = "repartidores"
REPLY_TIMEOUT_S = 60.0
# Synthetic candidate messages: no personal data, varied length.
TURN_TEXTS = (
    "Hola, me interesa el puesto de repartidor.",
    "Vivo cerca de Ruzafa, en València.",
    "Tengo moto propia y carné en vigor.",
    "Prefiero turnos de tarde, aunque puedo rotar.",
    "Llevo dos años haciendo repartos en bicicleta y en moto.",
    "¿Cuántas horas a la semana serían?",
    "Puedo empezar la semana que viene.",
    "Sí, el resumen es correcto.",
)
# Every external integration is forced off so the child processes cannot reach
# a provider even if the developer .env enables one.
SAFE_ENV = {
    "LLM_MODE": "fake",
    "JEV_MODE": "off",
    "AI_GATEWAY_API_KEY_FILE": "",
    "OPENAI_API_KEY": "",
    "OPENAI_API_KEY_FILE": "",
    "GPT_LIVE_ENABLED": "false",
    "GPT_LIVE_VOICE_ID": "",
    "META_SEND_ENABLED": "false",
    "TWILIO_CONFIG_FILE": "",
    "TWILIO_VOICE_CONFIG_FILE": "",
    "TWILIO_SMS_CONFIG_FILE": "",
    "ELEVEN_DIRECT_AGENTS_FILE": "",
    "ELEVENLABS_API_KEY_FILE": "",
    "PUBLISH_REQUIRES_EVALS": "false",
    "DJANGO_DEBUG": "false",
    "DJANGO_PRODUCTION": "false",
    "DJANGO_SETTINGS_MODULE": "web.settings",
}


# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/unit/test_loadtest.py)
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float:
    """Linear-interpolated percentile (same convention as numpy's default)."""
    if not values:
        raise ValueError("percentile of an empty sample")
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    p50: float
    p95: float
    p99: float
    max: float


def summarize(values: Sequence[float]) -> Summary:
    if not values:
        return Summary(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return Summary(
        n=len(values),
        mean=sum(values) / len(values),
        p50=percentile(values, 50),
        p95=percentile(values, 95),
        p99=percentile(values, 99),
        max=max(values),
    )


def parse_levels(raw: str) -> list[int]:
    levels = [int(part) for part in raw.split(",") if part.strip()]
    if not levels or any(level < 1 for level in levels):
        raise argparse.ArgumentTypeError("levels must be positive integers, e.g. 1,10,20")
    return levels


def isolation_error(database_url: str, redis_url: str) -> str | None:
    """Return why the target is unsafe, or None when it is an isolated copy."""
    database = urlsplit(database_url).path.lstrip("/")
    if not database.startswith("screening_"):
        return f"database {database or '(none)'!r} is not an isolated screening_<name> copy"
    redis = urlsplit(redis_url)
    index = redis.path.lstrip("/") or "0"
    if not index.isdigit() or int(index) == 0:
        return "Redis index 0 belongs to the shared stack; use a dedicated index"
    return None


def synthetic_ip(index: int) -> str:
    """Distinct client per virtual candidate in 198.18.0.0/15 (RFC 2544 benchmarking)."""
    if not 0 <= index < 2**17:
        raise ValueError("index outside the benchmarking range")
    return f"198.{18 + index // 65536}.{index // 256 % 256}.{index % 256}"


def ingest_headers(
    root: str, slug: str, path: str, body: bytes, timestamp: int, nonce: str
) -> dict[str, str]:
    """Signed ingest headers; mirrors web.conversations.ingest_auth (checked by a unit test)."""
    key = hmac.new(root.encode(), f"ingest-v1:{slug}".encode(), hashlib.sha256).hexdigest()
    canonical = "\n".join([str(timestamp), nonce, "POST", path, hashlib.sha256(body).hexdigest()])
    digest = hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Ingest-Timestamp": str(timestamp),
        "X-Ingest-Nonce": nonce,
        "X-Ingest-Signature": digest,
        "Content-Type": "application/json",
    }


@dataclass(frozen=True)
class CapacityInputs:
    """Planning inputs. Measured values come from local traces (see the deployment doc)."""

    candidates_per_week: int = 10_000
    inbound_turns: float = 10.0
    peak_factor: float = 10.0  # busiest hour / weekly mean hour
    text_session_minutes: float = 8.0
    turn_seconds: float = 8.0  # p50 real turn (conversador + guard), measured ~6-7 s
    llm_usd_per_turn: float = 0.00041  # measured: conversador + guard + Jev share
    evaluator_usd: float = 0.00087  # measured: one evaluator call per completed session
    completion_rate: float = 0.8
    conversador_calls_per_turn: float = 1.3  # measured 1.04 on web; tool loops add calls
    guard_calls_per_turn: float = 1.3  # worst case JEV_MODE=off: Luna reviews every reply
    conversador_reserved_tokens: int = 4_096  # max(max_completion_tokens, input estimate)
    guard_reserved_tokens: int = 2_300
    evaluator_reserved_tokens: int = 8_192
    whatsapp_share: float = 0.7
    spain_share: float = 0.6
    templates_per_candidate: float = 1.5  # first contact + reminders outside the window
    wa_rate_es: float = 0.0230  # USD per utility/service message, Spain (upper bound)
    wa_rate_mx: float = 0.0098  # USD per utility/service message, Mexico (upper bound)
    twilio_fee: float = 0.005  # USD per inbound or outbound message via Twilio
    voice_share: float = 0.1
    voice_minutes: float = 4.0
    voice_usd_per_min: float = 0.08  # ElevenAgents
    phone_share_of_voice: float = 0.5
    phone_usd_per_min: float = 0.0530  # Twilio ES mobile EEA 0.0486 + Media Streams 0.0044
    handoff_rate: float = 0.10
    handoff_minutes: float = 5.0
    proposal_rate: float = 0.45  # completed sessions whose decision needs a human
    proposal_minutes: float = 1.0


def capacity(inputs: CapacityInputs) -> dict[str, float]:
    i = inputs
    weekly_turns = i.candidates_per_week * i.inbound_turns
    mean_per_hour = i.candidates_per_week / (7 * 24)
    peak_candidates_hour = mean_per_hour * i.peak_factor
    peak_turns_min = peak_candidates_hour * i.inbound_turns / 60
    completed = i.candidates_per_week * i.completion_rate
    reserved_per_turn = (
        i.conversador_calls_per_turn * i.conversador_reserved_tokens
        + i.guard_calls_per_turn * i.guard_reserved_tokens
    )
    peak_completions_min = peak_candidates_hour * i.completion_rate / 60
    wa_rate = i.spain_share * i.wa_rate_es + (1 - i.spain_share) * i.wa_rate_mx
    wa_outbound = i.inbound_turns + i.templates_per_candidate
    wa_meta = wa_outbound * wa_rate
    wa_twilio = (wa_outbound + i.inbound_turns) * i.twilio_fee
    voice_call = i.voice_minutes * (
        i.voice_usd_per_min + i.phone_share_of_voice * i.phone_usd_per_min
    )
    llm_candidate = i.inbound_turns * i.llm_usd_per_turn + i.completion_rate * i.evaluator_usd
    human_hours = (
        completed * i.proposal_rate * i.proposal_minutes
        + i.candidates_per_week * i.handoff_rate * i.handoff_minutes
    ) / 60
    return {
        "weekly_turns": weekly_turns,
        "mean_candidates_per_hour": mean_per_hour,
        "peak_candidates_per_hour": peak_candidates_hour,
        "peak_turns_per_min": peak_turns_min,
        "peak_active_conversations": peak_candidates_hour * i.text_session_minutes / 60,
        "peak_turns_in_flight": peak_turns_min / 60 * i.turn_seconds,
        "peak_rpm": peak_turns_min * (i.conversador_calls_per_turn + i.guard_calls_per_turn)
        + peak_completions_min,
        "peak_tpm_reserved": peak_turns_min * reserved_per_turn
        + peak_completions_min * i.evaluator_reserved_tokens,
        "peak_wa_messages_per_s": peak_turns_min / 60 * (2 * i.whatsapp_share),
        "llm_usd_per_candidate": llm_candidate,
        "wa_meta_usd_per_wa_candidate": wa_meta,
        "wa_twilio_fee_usd_per_wa_candidate": wa_twilio,
        "voice_usd_per_voice_candidate": voice_call,
        "weekly_llm_usd": llm_candidate * i.candidates_per_week,
        "weekly_wa_meta_usd": wa_meta * i.candidates_per_week * i.whatsapp_share,
        "weekly_wa_twilio_fee_usd": wa_twilio * i.candidates_per_week * i.whatsapp_share,
        "weekly_voice_minutes": i.candidates_per_week * i.voice_share * i.voice_minutes,
        "weekly_voice_usd": voice_call * i.candidates_per_week * i.voice_share,
        "weekly_handoffs": i.candidates_per_week * i.handoff_rate,
        "weekly_proposals": completed * i.proposal_rate,
        "weekly_recruiter_hours": human_hours,
    }


def markdown_table(rows: Sequence[dict[str, Any]], columns: Sequence[tuple[str, str]]) -> str:
    header = "| " + " | ".join(title for _, title in columns) + " |"
    rule = "|" + "|".join("---:" for _ in columns) + "|"
    body = ["| " + " | ".join(str(row.get(key, "")) for key, _ in columns) + " |" for row in rows]
    return "\n".join([header, rule, *body])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="create, migrate and seed the isolated database")
    chat = commands.add_parser("chat", help="drive the chat path at several concurrency levels")
    chat.add_argument("--levels", type=parse_levels, default=[1, 10, 20, 40])
    chat.add_argument("--turns", type=int, default=8, help="candidate messages per session")
    chat.add_argument("--sessions-per-user", type=int, default=3)
    chat.add_argument("--think-ms", type=int, default=0, help="pause between turns")
    chat.add_argument("--web-workers", type=int, default=1, help="uvicorn processes")
    queue = commands.add_parser("queue", help="drain noop jobs with N worker processes")
    queue.add_argument("--jobs", type=int, default=2000)
    queue.add_argument("--workers", type=parse_levels, default=[1, 2, 4])
    commands.add_parser("capacity", help="print the 10k/week planning model")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    for name in ("turns", "sessions_per_user", "web_workers", "jobs"):
        if getattr(args, name, 1) < 1:
            raise SystemExit(f"--{name.replace('_', '-')} must be at least 1")
    if getattr(args, "think_ms", 0) < 0:
        raise SystemExit("--think-ms cannot be negative")
    return args


# ---------------------------------------------------------------------------
# Environment and child processes
# ---------------------------------------------------------------------------


def isolated_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    database_url = os.environ.get("DATABASE_URL", "")
    redis_url = os.environ.get("REDIS_URL", "")
    if problem := isolation_error(database_url, redis_url):
        raise SystemExit(f"Refusing to run: {problem}. Use the isolation wrapper.")
    env = dict(os.environ)
    env.update(SAFE_ENV)
    env["PYTHONPATH"] = str(ROOT / "src")
    env.update(extra or {})
    return env


def machine() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
    }


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def manage(env: dict[str, str], *args: str) -> None:
    subprocess.run([sys.executable, "manage.py", *args], cwd=ROOT, env=env, check=True)


def ensure_database(database_url: str) -> None:
    import psycopg
    from psycopg import sql

    parts = urlsplit(database_url)
    name = parts.path.lstrip("/")
    maintenance = parts._replace(path="/postgres").geturl()
    with psycopg.connect(maintenance, autocommit=True) as connection:
        exists = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        if exists.fetchone() is None:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print(f"Created database {name}")


def prepare() -> None:
    env = isolated_env({"SEED_PASSWORD": secrets.token_urlsafe(18)})
    ensure_database(env["DATABASE_URL"])
    manage(env, "migrate", "--noinput", "-v", "0")
    manage(env, "seed_local")


# ---------------------------------------------------------------------------
# Chat load
# ---------------------------------------------------------------------------


@dataclass
class Samples:
    ingest_ms: list[float] = field(default_factory=list)
    open_ms: list[float] = field(default_factory=list)
    first_ms: list[float] = field(default_factory=list)
    turn_ms: list[float] = field(default_factory=list)
    errors: Counter[str] = field(default_factory=Counter)


class TurnFailed(Exception):
    pass


async def wait_for_reply(ws: Any) -> None:
    async with asyncio.timeout(REPLY_TIMEOUT_S):
        while True:
            event = json.loads(await ws.recv())
            if event.get("type") == "message" and event["message"]["direction"] == "out":
                return
            if event.get("type") == "error":
                raise TurnFailed(str(event.get("code")))


async def candidate(
    client: Any, port: int, root: str, index: int, turns: int, think: float, out: Samples
) -> None:
    from websockets.asyncio.client import connect
    from websockets.typing import Origin

    path = f"/api/v1/ingest/{TENANT}/applications"
    body = json.dumps(
        {
            "external_id": f"lt-{uuid.uuid4().hex[:20]}",
            "candidate_name": f"Persona sintética {index}",
            "phone": "",
            "city": "València",
            "job_ref": OFFER,
        }
    ).encode()
    headers = ingest_headers(root, TENANT, path, body, int(time.time()), secrets.token_hex(12))
    started = time.perf_counter()
    response = await client.post(path, content=body, headers=headers)
    response.raise_for_status()
    out.ingest_ms.append((time.perf_counter() - started) * 1000)
    token = response.json()["candidate_link"].rsplit("/c/", 1)[1]
    started = time.perf_counter()
    response = await client.post(
        "/api/v1/chat/sessions",
        json={"token": token},
        headers={"X-Forwarded-For": synthetic_ip(index)},
    )
    response.raise_for_status()
    out.open_ms.append((time.perf_counter() - started) * 1000)
    opened = response.json()
    url = (
        f"ws://127.0.0.1:{port}/ws/chat/{opened['session_id']}/?ticket={opened['websocket_token']}"
    )
    started = time.perf_counter()
    async with connect(url, origin=Origin(f"http://localhost:{port}"), open_timeout=30) as ws:
        await wait_for_reply(ws)
        out.first_ms.append((time.perf_counter() - started) * 1000)
        for turn in range(turns):
            message = {
                "type": "message",
                "text": TURN_TEXTS[turn % len(TURN_TEXTS)],
                "client_message_id": str(uuid.uuid4()),
            }
            started = time.perf_counter()
            await ws.send(json.dumps(message))
            await wait_for_reply(ws)
            out.turn_ms.append((time.perf_counter() - started) * 1000)
            if think:
                await asyncio.sleep(think)


async def user_loop(
    client: Any, port: int, root: str, first_index: int, args: argparse.Namespace, out: Samples
) -> None:
    for offset in range(args.sessions_per_user):
        try:
            await candidate(
                client, port, root, first_index + offset, args.turns, args.think_ms / 1000, out
            )
        except Exception as exc:  # A failed session is a result, not a crash of the run.
            out.errors[type(exc).__name__ + (f":{exc}" if isinstance(exc, TurnFailed) else "")] += 1


async def wait_healthy(client: httpx.AsyncClient, server: asyncio.subprocess.Process) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if server.returncode is not None:
            raise SystemExit("The load-test server exited during startup; see its log.")
        # Connection errors are expected while uvicorn binds the port.
        with contextlib.suppress(httpx.HTTPError):
            if (await client.get("/api/v1/health")).status_code == 200:
                return
        await asyncio.sleep(0.5)
    raise SystemExit("The load-test server did not become healthy in 90 s.")


async def run_chat(args: argparse.Namespace) -> dict[str, Any]:
    port, root = free_port(), secrets.token_urlsafe(32)
    env = isolated_env(
        {
            "INGEST_TOKEN": root,
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
            "SEED_PASSWORD": secrets.token_urlsafe(18),
            "CHAT_BASE_URL": f"http://localhost:{port}",
        }
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log = (OUTPUT_DIR / f"server-{stamp}.log").open("wb")
    server = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "uvicorn",
        "web.asgi:application",
        "--app-dir",
        "src",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        str(args.web_workers),
        "--no-access-log",
        "--log-level",
        "warning",
        "--lifespan",
        "off",
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    results: list[dict[str, Any]] = []
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=500)
    try:
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=REPLY_TIMEOUT_S, limits=limits
        ) as client:
            await wait_healthy(client, server)
            next_index = 0
            for level in args.levels:
                samples = Samples()
                started = time.perf_counter()
                await asyncio.gather(
                    *(
                        user_loop(
                            client,
                            port,
                            root,
                            next_index + u * args.sessions_per_user,
                            args,
                            samples,
                        )
                        for u in range(level)
                    )
                )
                wall = time.perf_counter() - started
                next_index += level * args.sessions_per_user
                turns = len(samples.turn_ms)
                row = {
                    "concurrency": level,
                    "sessions_ok": len(samples.first_ms),
                    "errors": dict(samples.errors),
                    "turns": turns,
                    "wall_s": round(wall, 2),
                    "turns_per_s": round(turns / wall, 1) if wall else 0,
                    "turn_ms": asdict(summarize(samples.turn_ms)),
                    "first_message_ms": asdict(summarize(samples.first_ms)),
                    "open_ms": asdict(summarize(samples.open_ms)),
                    "ingest_ms": asdict(summarize(samples.ingest_ms)),
                }
                results.append(row)
                print(
                    f"c={level:>3} turns={turns:>4} {row['turns_per_s']:>6} turns/s "
                    f"p50={row['turn_ms']['p50']:.0f} ms p95={row['turn_ms']['p95']:.0f} ms "
                    f"errors={sum(samples.errors.values())}",
                    flush=True,
                )
    finally:
        if server.returncode is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                async with asyncio.timeout(20):
                    await server.wait()
            except TimeoutError:
                os.killpg(server.pid, signal.SIGKILL)
        log.close()
    report = {
        "kind": "chat",
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": machine(),
        "llm_mode": "fake",
        "web_workers": args.web_workers,
        "turns_per_session": args.turns,
        "sessions_per_user": args.sessions_per_user,
        "think_ms": args.think_ms,
        "results": results,
    }
    path = OUTPUT_DIR / f"chat-{stamp}-w{args.web_workers}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Raw results: {path.relative_to(ROOT)}")
    return report


# ---------------------------------------------------------------------------
# Queue drain benchmark
# ---------------------------------------------------------------------------


def run_queue(args: argparse.Namespace) -> dict[str, Any]:
    env = isolated_env(
        {"DJANGO_SECRET_KEY": secrets.token_urlsafe(48), "SEED_PASSWORD": secrets.token_urlsafe(18)}
    )
    os.environ.update(env)
    sys.path.insert(0, str(ROOT / "src"))
    import django

    django.setup()
    from django.db.models import Max, Min

    from web.jobs.models import Job

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    with (OUTPUT_DIR / f"worker-{stamp}.log").open("wb") as log:
        for count in args.workers:
            run = uuid.uuid4().hex
            mine = Job.objects.filter(kind="noop", payload__loadtest=run)
            Job.objects.bulk_create(
                [Job(kind="noop", payload={"loadtest": run}) for _ in range(args.jobs)],
                batch_size=1000,
            )
            started = time.monotonic()
            workers = [
                subprocess.Popen(
                    [sys.executable, "manage.py", "run_worker"],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                for _ in range(count)
            ]
            ages: list[float] = []
            try:
                while time.monotonic() - started < 300:
                    oldest = mine.filter(status="queued").aggregate(Min("created_at"))
                    if (
                        oldest["created_at__min"] is None
                        and not mine.exclude(status="done").exists()
                    ):
                        break
                    if oldest["created_at__min"] is not None:
                        ages.append((datetime.now(UTC) - oldest["created_at__min"]).total_seconds())
                    time.sleep(0.25)
            finally:
                for worker in workers:
                    worker.send_signal(signal.SIGTERM)
                for worker in workers:
                    try:
                        worker.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        worker.kill()
            done = mine.filter(status="done")
            bounds = done.aggregate(first=Min("updated_at"), last=Max("updated_at"))
            latency = [
                (finished - created).total_seconds() * 1000
                for created, finished in done.values_list("created_at", "updated_at")
            ]
            span = (
                (bounds["last"] - bounds["first"]).total_seconds()
                if bounds["first"] and bounds["last"]
                else 0.0
            )
            row = {
                "workers": count,
                "jobs": args.jobs,
                "done": len(latency),
                "drain_s_including_startup": round(time.monotonic() - started, 2),
                "jobs_per_s_steady": round(len(latency) / span, 1) if span else 0,
                "enqueue_to_done_ms": asdict(summarize(latency)),
                "max_queue_age_s": round(max(ages), 2) if ages else 0,
            }
            results.append(row)
            print(
                f"workers={count} done={row['done']}/{args.jobs} "
                f"{row['jobs_per_s_steady']} jobs/s age_max={row['max_queue_age_s']} s",
                flush=True,
            )
    report = {
        "kind": "queue",
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": machine(),
        "results": results,
    }
    path = OUTPUT_DIR / f"queue-{stamp}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Raw results: {path.relative_to(ROOT)}")
    return report


def print_capacity() -> None:
    model = capacity(CapacityInputs())
    width = max(map(len, model))
    for key, value in model.items():
        print(f"{key:<{width}}  {value:,.4f}" if value < 10 else f"{key:<{width}}  {value:,.1f}")


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    if args.command == "prepare":
        prepare()
    elif args.command == "chat":
        asyncio.run(run_chat(args))
    elif args.command == "queue":
        run_queue(args)
    else:
        print_capacity()


if __name__ == "__main__":
    main()
