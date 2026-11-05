import csv
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from web.simulated.models import CalendarSlot, Place, Site
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant


def load_sites(tenant: Tenant, path: Path) -> int:
    count = 0
    with tenant_context(tenant.id), path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            Site.objects.update_or_create(
                tenant=tenant,
                external_id=row["external_id"],
                defaults={
                    "dataset": row["dataset"],
                    "name": row["name"],
                    "city": row["city"],
                    "country": row["country"],
                    "timezone": row["timezone"],
                    "lat": float(row["lat"]),
                    "lng": float(row["lng"]),
                    "vacancies": {
                        shift: int(row[f"vacancies_{shift}"])
                        for shift in ("morning", "afternoon", "night", "rotating")
                    },
                    "has_calendar": row["has_calendar"].lower() == "true",
                    "address": (row.get("address") or "").strip(),
                },
            )
            count += 1
    return count


def load_places(tenant: Tenant, path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    with tenant_context(tenant.id):
        Place.objects.filter(tenant=tenant, dataset__in={r["dataset"] for r in rows}).delete()
        Place.objects.bulk_create(
            [
                Place(
                    tenant=tenant,
                    dataset=row["dataset"],
                    name=row["name"],
                    aliases=[a for a in row["aliases"].split("|") if a],
                    city=row["city"],
                    lat=float(row["lat"]),
                    lng=float(row["lng"]),
                )
                for row in rows
            ]
        )
    return len(rows)


def ensure_slots(tenant: Tenant, now: datetime) -> int:
    count = 0
    with tenant_context(tenant.id):
        for site in Site.objects.filter(tenant=tenant, has_calendar=True):
            if CalendarSlot.objects.filter(site=site, starts_at__gt=now).exists():
                continue
            zone = ZoneInfo(site.timezone)
            date = now.astimezone(zone).date()
            days = 0
            slots: list[CalendarSlot] = []
            while days < 10:
                date += timedelta(days=1)
                if date.weekday() >= 5:
                    continue
                days += 1
                slots.extend(
                    CalendarSlot(
                        tenant=tenant,
                        site=site,
                        starts_at=datetime.combine(date, time(hour), tzinfo=zone),
                    )
                    for hour in (10, 11, 12, 16, 17)
                )
            CalendarSlot.objects.bulk_create(slots)
            count += len(slots)
    return count
