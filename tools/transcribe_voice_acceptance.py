"""Explicit paid, synthetic-only intelligibility check of recorded browser output."""

import json
import sys
from pathlib import Path

import httpx


def main() -> None:
    audio = Path(sys.argv[1])
    if audio.parent != Path(".local") or not audio.name.startswith("direct-"):
        raise SystemExit("Only the synthetic direct-voice fixtures are allowed")
    key = Path(".runtime-secrets/elevenlabs_api_key").read_text().strip()
    with audio.open("rb") as stream:
        result = httpx.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": key},
            data={"model_id": "scribe_v2", "language_code": "es", "tag_audio_events": "false"},
            files={"file": (audio.name, stream, "audio/wav")},
            timeout=90,
        )
    if not result.is_success:
        print(json.dumps({"http_status": result.status_code, "transcribed": False}))
        return
    data = result.json()
    audio.with_suffix(".transcript.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {"transcribed": True, "language": data.get("language_code"), "text": data.get("text")},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
