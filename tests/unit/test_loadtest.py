import argparse
from dataclasses import replace

import pytest
from tools.loadtest import (
    CapacityInputs,
    capacity,
    ingest_headers,
    isolation_error,
    markdown_table,
    parse_args,
    parse_levels,
    percentile,
    summarize,
    synthetic_ip,
)

from web.conversations.ingest_auth import signature, tenant_key


def test_percentile_interpolates_linearly() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 0) == 10
    assert percentile(values, 50) == 25
    assert percentile(values, 95) == pytest.approx(38.5)
    assert percentile(values, 100) == 40
    assert percentile([7.0], 99) == 7


@pytest.mark.parametrize("values, p", [([], 50), ([1.0], -1), ([1.0], 101)])
def test_percentile_rejects_invalid_input(values: list[float], p: float) -> None:
    with pytest.raises(ValueError):
        percentile(values, p)


def test_summary_of_unordered_and_empty_samples() -> None:
    summary = summarize([5.0, 1.0, 3.0, 2.0, 4.0])
    assert (summary.n, summary.mean, summary.p50, summary.max) == (5, 3.0, 3.0, 5.0)
    assert summary.p95 == pytest.approx(4.8)
    assert summarize([]).n == 0


def test_levels_and_arguments() -> None:
    assert parse_levels("1, 10,40") == [1, 10, 40]
    with pytest.raises(argparse.ArgumentTypeError):
        parse_levels("0,5")
    args = parse_args(["chat", "--levels", "2,4", "--turns", "3", "--web-workers", "2"])
    assert (args.levels, args.turns, args.web_workers, args.think_ms) == ([2, 4], 3, 2, 0)
    assert parse_args(["queue"]).workers == [1, 2, 4]
    assert parse_args(["capacity"]).command == "capacity"
    with pytest.raises(SystemExit):
        parse_args(["chat", "--turns", "0"])
    with pytest.raises(SystemExit):
        parse_args(["chat", "--think-ms", "-5"])


@pytest.mark.parametrize(
    "database, redis, safe",
    [
        ("postgres://u:p@localhost:5432/screening", "redis://localhost:6379/4", False),
        ("postgres://u:p@localhost:5432/screening_load", "redis://localhost:6379/0", False),
        ("postgres://u:p@localhost:5432/screening_load", "redis://localhost:6379", False),
        ("postgres://u:p@localhost:5432/other", "redis://localhost:6379/4", False),
        ("postgres://u:p@localhost:5432/screening_load", "redis://localhost:6379/4", True),
    ],
)
def test_refuses_shared_database_or_redis(database: str, redis: str, safe: bool) -> None:
    assert (isolation_error(database, redis) is None) is safe


def test_synthetic_clients_stay_in_benchmark_range() -> None:
    assert synthetic_ip(0) == "198.18.0.0"
    assert synthetic_ip(257) == "198.18.1.1"
    assert synthetic_ip(65536) == "198.19.0.0"
    with pytest.raises(ValueError):
        synthetic_ip(2**17)


def test_ingest_headers_match_server_signature() -> None:
    body = b'{"external_id": "lt-1"}'
    path = "/api/v1/ingest/grupo_sazon/applications"
    headers = ingest_headers("root", "grupo_sazon", path, body, 1_800_000_000, "n" * 24)
    expected = signature(tenant_key("root", "grupo_sazon"), "1800000000", "n" * 24, path, body)
    assert headers["X-Ingest-Signature"] == expected
    assert headers["X-Ingest-Timestamp"] == "1800000000"


def test_capacity_model_for_ten_thousand_per_week() -> None:
    model = capacity(CapacityInputs())
    assert model["weekly_turns"] == 100_000
    assert model["peak_turns_per_min"] == pytest.approx(10_000 / 168 * 10 * 10 / 60)
    assert model["peak_active_conversations"] == pytest.approx(10_000 / 168 * 10 * 8 / 60)
    assert model["llm_usd_per_candidate"] == pytest.approx(10 * 0.00041 + 0.8 * 0.00087)
    doubled = capacity(replace(CapacityInputs(), candidates_per_week=20_000))
    for key in ("weekly_llm_usd", "weekly_voice_usd", "weekly_recruiter_hours"):
        assert doubled[key] == pytest.approx(2 * model[key])
    assert doubled["llm_usd_per_candidate"] == model["llm_usd_per_candidate"]


def test_markdown_table() -> None:
    table = markdown_table([{"c": 1, "p95": 12.5}], [("c", "Concurrencia"), ("p95", "p95 ms")])
    assert table.splitlines() == ["| Concurrencia | p95 ms |", "|---:|---:|", "| 1 | 12.5 |"]
