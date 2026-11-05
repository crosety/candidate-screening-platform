"""Measure real browser PCM playback and export a short synthetic acceptance sample."""

import array
import json
import math
import sys
import wave
from pathlib import Path


def main() -> None:
    path = Path(sys.argv[1])
    if path.parent != Path(".local") or not path.name.startswith("direct-"):
        raise SystemExit("Only synthetic acceptance audio is allowed")
    with wave.open(str(path)) as source:
        params = source.getparams()
        if params.nchannels != 1 or params.sampwidth != 2 or params.framerate != 16000:
            raise SystemExit("Unexpected PCM format")
        raw = source.readframes(source.getnframes())
    samples = array.array("h", raw)
    if sys.byteorder != "little":
        samples.byteswap()
    rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
    clipped = sum(abs(v) >= 32760 for v in samples)
    report = {
        "seconds": len(samples) / 16000,
        "rms_dbfs": round(20 * math.log10(max(rms, 1) / 32768), 2),
        "peak_dbfs": round(20 * math.log10(max(1, max(map(abs, samples))) / 32768), 2),
        "clipped_fraction": clipped / max(1, len(samples)),
        "non_silent_fraction": sum(abs(v) > 100 for v in samples) / max(1, len(samples)),
        "scope": "Generated PCM delivered to browser, not physical speaker/microphone quality",
    }
    target = path.with_name(path.stem + "-sample.wav")
    with wave.open(str(target), "wb") as output:
        output.setparams(params)
        output.writeframes(raw[-20 * 32000 :])
    path.with_suffix(".audio-metrics.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({**report, "sample": str(target)}))


if __name__ == "__main__":
    main()
