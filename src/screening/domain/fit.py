"""Explainable criterion coverage, not a prediction or an employment decision."""

from typing import Any

from screening.domain.profile import OfferDefinition


def assess_fit(offer: OfferDefinition | None, fields: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if offer is None:
        return {"score": None, "coverage": 0, "requirements": "pending", "items": []}
    for criterion in offer.criteria:
        item = fields.get(criterion.field) or {}
        value, evidence = item.get("value"), item.get("evidence")
        state = "unknown"
        if value is not None and value != "" and evidence and item.get("confidence") != "low":
            if criterion.operator == "present":
                matched = True
            elif criterion.operator == "minimum":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    items.append(
                        {
                            **criterion.model_dump(),
                            "state": "unknown",
                            "value": value,
                            "evidence": evidence,
                        }
                    )
                    continue
                matched = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and isinstance(criterion.expected, (int, float))
                    and value >= criterion.expected
                )
            else:
                matched = type(value) is type(criterion.expected) and value == criterion.expected
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and isinstance(criterion.expected, (int, float))
                    and not isinstance(criterion.expected, bool)
                ):
                    matched = value == criterion.expected
                if isinstance(value, str) and isinstance(criterion.expected, str):
                    matched = value.strip().casefold() == criterion.expected.strip().casefold()
            state = "met" if matched else "not_met"
        items.append(
            {**criterion.model_dump(), "state": state, "value": value, "evidence": evidence}
        )
    known = [i for i in items if i["state"] != "unknown"]
    required = [i for i in items if i["required"]]
    requirements = (
        "not_met"
        if any(i["state"] == "not_met" for i in required)
        else "pending"
        if any(i["state"] == "unknown" for i in required)
        else "met"
        if required
        else "not_configured"
    )
    score = None
    if items and len(known) == len(items):
        score = round(
            100
            * sum(i["weight"] for i in items if i["state"] == "met")
            / sum(i["weight"] for i in items)
        )
    return {
        "score": score,
        "coverage": round(100 * len(known) / len(items)) if items else 0,
        "requirements": requirements,
        "items": items,
    }
