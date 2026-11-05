import re


def sentences(text: str) -> list[str]:
    """Buffer final text; never expose unchecked partial model tokens to TTS."""
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]
