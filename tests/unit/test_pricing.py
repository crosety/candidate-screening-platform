import pytest

from adapters.llm.pricing import cost_usd_micros


@pytest.mark.parametrize(
    "model, cost", [("sonnet", 2_000_000), ("haiku", 100_000), ("opus", 4_000_000)]
)
def test_input_price(model: str, cost: int) -> None:
    assert cost_usd_micros(f"eu.anthropic.claude-{model}-5-5", 1_000_000, 0, 0, 0) == cost


def test_unknown_model() -> None:
    with pytest.raises(KeyError):
        cost_usd_micros("unknown", 1, 0, 0, 0)


def test_luna_standard_price_includes_cache_writes() -> None:
    assert cost_usd_micros("gpt-6-luna", 1_000_000, 1_000_000, 1_000_000, 1_000_000) == 735_000
