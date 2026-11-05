import hashlib
import json
from typing import Any

from screening.domain.evals import Persona
from screening.domain.profile import ProfileContent


def eval_snapshot(profile: ProfileContent, personas: list[Persona]) -> dict[str, Any]:
    content = json.dumps(profile.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    people = [p.model_dump(mode="json") for p in personas]
    return {
        "profile_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "personas_sha256": hashlib.sha256(
            json.dumps(people, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "threshold": profile.eval_threshold,
        "expected_verdicts": {p.key: p.expected_verdict for p in personas},
    }
