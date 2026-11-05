PRICES: dict[str, tuple[float, float, float, float]] = {
    # Verified 2026-10-23: https://developers.openai.com/api/docs/pricing
    # Standard, <=272K input, USD/M tokens: input, output, cached read, 30m write.
    # This is a token estimate, not a bill; 12h caching and regional premiums are excluded.
    "gpt-6-luna": (0.10, 0.50, 0.01, 0.125),
    # Verified 2026-10-31 against the same Standard pricing source.
    "gpt-6.1-terra": (1.00, 5.00, 0.05, 1.25),
    "sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "opus-5-5": (4.0, 20.0, 0.20, 5.0),
}


def cost_usd_micros(
    model_id: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int
) -> int:
    for key, prices in PRICES.items():
        if key in model_id:
            return round(
                input_tokens * prices[0]
                + output_tokens * prices[1]
                + cache_read * prices[2]
                + cache_write * prices[3]
            )
    raise KeyError(model_id)
