"""Read-only acceptance evidence. Private case links and provider keys never leave files."""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "web.settings")
    import django

    django.setup()
    import httpx

    from web.conversations.models import Event, Message, Session
    from web.decisions.models import Decision, OutboxItem, Result
    from web.simulated.models import AtsRecord, CalendarSlot
    from web.tenancy.context import tenant_context
    from web.tracing.models import CapabilityCall, LlmCall

    cases = json.loads(Path(sys.argv[1]).read_text())
    provider = "--provider" in sys.argv
    report: dict[str, Any] = {}
    logging.getLogger("httpx").setLevel(logging.WARNING)
    key = Path(".runtime-secrets/elevenlabs_api_key").read_text().strip() if provider else ""
    for name, case in cases.items():
        tenant, sid = UUID(case["tenant_id"]), UUID(case["session_id"])
        with tenant_context(tenant):
            s = Session.objects.get(pk=sid)
            item: dict[str, Any] = {
                "session": str(sid),
                "status": s.status,
                "messages": list(
                    Message.objects.filter(session=s)
                    .order_by("created_at")
                    .values("direction", "channel", "text", "created_at")
                ),
                "events": list(
                    Event.objects.filter(session=s)
                    .order_by("created_at")
                    .values("type", "payload", "created_at")
                ),
                "capabilities": list(
                    CapabilityCall.objects.filter(session_id=sid).order_by("created_at").values()
                ),
                "llm": list(LlmCall.objects.filter(session_id=sid).order_by("created_at").values()),
                "result": list(Result.objects.filter(session=s).values()),
                "decisions": list(Decision.objects.filter(session=s).values()),
                "outbox": list(OutboxItem.objects.filter(session=s).values()),
                "ats": list(
                    AtsRecord.objects.filter(external_id=s.application.external_id).values()
                ),
                "bookings": list(
                    CalendarSlot.objects.filter(
                        booked_application_external_id=s.application.external_id
                    ).values()
                ),
            }
        if provider:
            item["provider"] = []
            ids = [
                e["payload"]["conversation_id"]
                for e in item["events"]
                if e["type"] == "eleven_direct_ended" and e["payload"].get("conversation_id")
            ]
            for conv in ids:
                r = httpx.get(
                    f"https://api.elevenlabs.io/v1/convai/conversations/{conv}",
                    headers={"xi-api-key": key},
                    timeout=20,
                )
                if r.is_success:
                    data = r.json()
                    item["provider"].append(
                        {
                            "conversation_id": conv,
                            "status": data.get("status"),
                            "metadata": data.get("metadata"),
                            "analysis": data.get("analysis"),
                            "transcript": data.get("transcript"),
                        }
                    )
                else:
                    item["provider"].append({"conversation_id": conv, "http_status": r.status_code})
        report[name] = item
    output = Path(".local/direct-audit-" + Path(sys.argv[1]).stem + ".json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(
        json.dumps(
            {
                name: {
                    "status": i["status"],
                    "messages": len(i["messages"]),
                    "tools": [
                        e["payload"].get("tool")
                        for e in i["events"]
                        if e["type"] == "voice_tool_started"
                    ],
                    "llm_cost_micros": sum(c["cost_usd_micros"] for c in i["llm"]),
                }
                for name, i in report.items()
            }
        )
    )


if __name__ == "__main__":
    main()
