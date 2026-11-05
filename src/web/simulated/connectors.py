import math
import re
import unicodedata
from datetime import UTC, date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError

from screening.application.ports import Clock
from screening.domain.errors import NotAllowed, NotFound
from screening.domain.profile import Connection
from web.simulated.models import AtsRecord, CalendarOperation, CalendarSlot, Place, Site
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant

WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def normalize(text: str) -> str:
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
    )
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def haversine_km(lat: float, lng: float, other_lat: float, other_lng: float) -> float:
    first, second = math.radians(lat), math.radians(other_lat)
    delta_lat, delta_lng = second - first, math.radians(other_lng - lng)
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(first) * math.cos(second) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(min(1.0, value)))


def booking_data(slot: CalendarSlot) -> dict[str, Any]:
    data = slot_data(slot)
    return {
        "slot_id": data["slot_id"],
        "starts_at_local": data["starts_at_local"],
        "site_name": data["site_name"],
        "site_id": slot.site.external_id,
        "site_address": slot.site.address,
        "site_timezone": slot.site.timezone,
        "starts_at": slot.starts_at.astimezone(UTC).isoformat(),
        "duration_minutes": 15,
    }


def slot_data(slot: CalendarSlot) -> dict[str, Any]:
    local = slot.starts_at.astimezone(ZoneInfo(slot.site.timezone))
    return {
        "slot_id": str(slot.id),
        "starts_at_local": f"{WEEKDAYS[local.weekday()]} {local:%d/%m/%Y %H:%M}",
        "site_name": slot.site.name,
    }


class _SimConnector:
    def __init__(self, tenant_slug: str, connection: Connection, clock: Clock) -> None:
        self.tenant_slug = tenant_slug
        self.connection = connection
        self.clock = clock

    async def call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        return await sync_to_async(self._call)(operation, args)

    def _call(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            tenant = Tenant.objects.get(slug=self.tenant_slug)
        except Tenant.DoesNotExist as exc:
            raise NotFound("Cliente no encontrado") from exc
        with tenant_context(tenant.id):
            return self._operate(tenant, operation, args)

    def _operate(self, tenant: Tenant, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(f"Operación desconocida: {operation}")


class SimSitesConnector(_SimConnector):
    def _operate(self, tenant: Tenant, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        if operation == "inspect":
            dataset_sites = Site.objects.filter(
                tenant=tenant, dataset=self.connection.settings["dataset"]
            )
            cities = ", ".join(sorted(set(dataset_sites.values_list("city", flat=True))))
            return {
                "structure": [
                    f"Conjunto de datos: {self.connection.settings['dataset']}",
                    f"{dataset_sites.count()} locales en {cities}",
                    "Columnas: id, nombre, ciudad, coordenadas, "
                    "vacantes por turno (mañana, tarde, noche, rotativo), agenda",
                ]
            }
        if operation == "status":
            sites = list(
                Site.objects.filter(tenant=tenant, dataset=self.connection.settings["dataset"])
            )
            available_sites = [site for site in sites if sum(site.vacancies.values()) > 0]
            return {
                "summary": f"{len(sites)} locales, {len(available_sites)} con vacantes",
                "warnings": [
                    {
                        "code": "site_without_calendar",
                        "text": f"«{site.name}» tiene vacantes "
                        "pero no tiene agenda de entrevistas.",
                    }
                    for site in available_sites
                    if args.get("schedule_enabled") and not site.has_calendar
                ],
            }
        if operation != "find_openings":
            return super()._operate(tenant, operation, args)
        near = normalize(str(args["near"]))
        dataset = self.connection.settings["dataset"]
        places = list(Place.objects.filter(tenant=tenant, dataset=dataset))
        names = {p.id: [normalize(str(n)) for n in [p.name, *p.aliases]] for p in places}
        exact = [p for p in places if near in names[p.id]]
        matches = exact or [
            p for p in places if any(f" {name} " in f" {near} " for name in names[p.id])
        ]
        if not matches:
            return {"status": "not_found", "places": [], "openings": []}
        if len({p.city for p in matches}) > 1:
            by_city = [p for p in matches if f" {normalize(p.city)} " in f" {near} "]
            matches = by_city or matches
        if len({p.city for p in matches}) > 1:
            return {
                "status": "ambiguous",
                "places": sorted(f"{p.name} ({p.city})" for p in matches),
                "openings": [],
            }
        specific = [p for p in matches if normalize(p.name) != normalize(p.city)]
        matches = specific or matches
        place = max(
            matches,
            key=lambda p: max((len(n) for n in names[p.id] if f" {n} " in f" {near} "), default=0),
        )
        speed = float(self.connection.settings["speed_kmh"])
        shift = args.get("shift", "any")
        if shift not in ("morning", "afternoon", "night", "rotating", "any"):
            raise NotAllowed("Turno no válido.")
        openings = []
        for site in Site.objects.filter(tenant=tenant, dataset=dataset):
            vacancies = (
                sum(site.vacancies.values()) if shift == "any" else site.vacancies.get(shift, 0)
            )
            if not vacancies:
                continue
            minutes = math.ceil(
                haversine_km(place.lat, place.lng, site.lat, site.lng) * 1.3 / speed * 60
            )
            openings.append(
                {
                    "site_id": site.external_id,
                    "site_name": site.name,
                    "city": site.city,
                    "minutes": minutes,
                    "vacancies": vacancies,
                    "shift": shift,
                    "origin": f"{place.name} ({place.city})",
                    "travel_mode": self.connection.settings.get("travel_mode", "unspecified"),
                    "estimate": True,
                    "timezone": site.timezone,
                    "country": site.country,
                }
            )
        openings.sort(key=lambda item: (item["minutes"], item["site_id"]))
        return {
            "status": "ok",
            "places": [f"{place.name} ({place.city})"],
            "openings": openings[:3],
        }


class SimAtsConnector(_SimConnector):
    def _operate(self, tenant: Tenant, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        if operation == "inspect":
            stages = ", ".join(
                value for key, value in self.connection.settings.items() if key.startswith("stage_")
            )
            keys = sorted(
                {key for row in AtsRecord.objects.filter(tenant=tenant) for key in row.fields}
            )
            return {"structure": [f"Etapas: {stages}", f"Campos en uso: {', '.join(keys)}"]}
        if operation == "status":
            count = AtsRecord.objects.filter(tenant=tenant).count()
            return {
                "summary": f"{count} candidatas en el ATS",
                "warnings": [],
            }
        if operation not in ("get_application", "write_result", "set_stage"):
            return super()._operate(tenant, operation, args)
        try:
            records = AtsRecord.objects.all()
            if operation != "get_application":
                records = records.select_for_update()
            record = records.get(
                tenant=tenant,
                external_id=args[
                    "external_id" if operation == "get_application" else "application_external_id"
                ],
            )
        except AtsRecord.DoesNotExist as exc:
            raise NotFound("Candidatura no encontrada") from exc
        if operation != "get_application":
            if operation == "write_result":
                record.fields.update(args["fields"])
            if args.get("stage"):
                record.stage = args["stage"]
            record.notes.append(
                {
                    "at": self.clock.now().isoformat(),
                    "text": args["summary" if operation == "write_result" else "note"],
                }
            )
            record.save(update_fields=["fields", "stage", "notes", "updated_at"])
            return {"status": "ok"}
        return {
            "candidate_name": record.candidate_name,
            "city": record.city,
            "job_ref": record.job_ref,
        }


class SimCalendarConnector(_SimConnector):
    def _operate(self, tenant: Tenant, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        if operation == "inspect":
            count = Site.objects.filter(tenant=tenant, has_calendar=True).count()
            return {
                "structure": [
                    "Huecos de entrevista de 1 hora, de lunes a viernes, "
                    "a las 10:00, 11:00, 12:00, 16:00 y 17:00",
                    f"{count} locales con agenda",
                ]
            }
        if operation == "status":
            total, warnings = 0, []
            for site in Site.objects.filter(tenant=tenant, has_calendar=True):
                zone = ZoneInfo(site.timezone)
                current_date = self.clock.now().astimezone(zone).date()
                dates: list[date] = []
                while len(dates) < 5:
                    current_date += timedelta(days=1)
                    if current_date.weekday() < 5:
                        dates.append(current_date)
                count = sum(
                    slot.starts_at.astimezone(zone).date() in dates
                    for slot in CalendarSlot.objects.filter(
                        tenant=tenant,
                        site=site,
                        starts_at__gt=self.clock.now(),
                        booked_application_external_id="",
                    )
                )
                total += count
                if not count:
                    warnings.append(
                        {
                            "code": "calendar_full",
                            "text": f"«{site.name}» no tiene huecos libres "
                            "en los próximos 5 días laborables.",
                        }
                    )
            return {
                "summary": f"{total} huecos libres en los próximos 5 días laborables",
                "warnings": warnings,
            }
        if operation == "list_slots":
            site = Site.objects.get(tenant=tenant, external_id=args["site_id"])
            if not site.has_calendar:
                return {"slots": []}
            slots = (
                CalendarSlot.objects.select_related("site")
                .filter(
                    site=site, booked_application_external_id="", starts_at__gt=self.clock.now()
                )
                .order_by("starts_at")[: max(1, min(int(args.get("limit", 3)), 20))]
            )
            return {"slots": [slot_data(slot) for slot in slots]}
        if operation in ("book_slot", "cancel_booking", "reschedule_booking"):
            # One tenant lock serializes every write: a candidate cannot hold two slots and
            # two candidates cannot take one slot, whatever the order of their requests.
            Tenant.objects.select_for_update().get(pk=tenant.pk)
            key = str(args.get("idempotency_key") or "")
            if key:
                done = CalendarOperation.objects.filter(tenant=tenant, idempotency_key=key).first()
                if done is not None:
                    if done.operation != operation:
                        raise NotAllowed("Clave de idempotencia reutilizada en otra operación.")
                    return {**done.result, "replayed": True}
            result = getattr(self, f"_{operation}")(tenant, args)
            if key and result.get("status") in ("booked", "cancelled", "rescheduled"):
                CalendarOperation.objects.create(
                    tenant=tenant, idempotency_key=key, operation=operation, result=result
                )
            return result
        return super()._operate(tenant, operation, args)

    def _book_slot(self, tenant: Tenant, args: dict[str, Any]) -> dict[str, Any]:
        owner = args["application_external_id"]
        if CalendarSlot.objects.filter(
            tenant=tenant, booked_application_external_id=owner
        ).exists():
            return {"status": "already_booked"}
        slot = self._slot(tenant, args["slot_id"])
        if slot.starts_at <= self.clock.now():
            return {"status": "expired"}
        if slot.booked_application_external_id:
            return {"status": "taken"}
        slot.booked_application_external_id = owner
        slot.save(update_fields=["booked_application_external_id"])
        return {"status": "booked", **booking_data(slot)}

    def _cancel_booking(self, tenant: Tenant, args: dict[str, Any]) -> dict[str, Any]:
        slot = self._slot(tenant, args["slot_id"])
        if slot.booked_application_external_id != args["application_external_id"]:
            # Never reveal or release another candidate's booking.
            return {"status": "not_owner"}
        slot.booked_application_external_id = ""
        slot.save(update_fields=["booked_application_external_id"])
        return {"status": "cancelled", "slot_id": str(slot.id)}

    def _reschedule_booking(self, tenant: Tenant, args: dict[str, Any]) -> dict[str, Any]:
        owner = args["application_external_id"]
        if str(args["slot_id"]) == str(args["new_slot_id"]):
            return {"status": "same_slot"}
        try:
            old, new = (
                self._slot(tenant, args["slot_id"]),
                self._slot(tenant, args["new_slot_id"]),
            )
        except NotFound:
            return {"status": "not_found"}
        if old.booked_application_external_id != owner:
            return {"status": "not_owner"}
        if new.starts_at <= self.clock.now():
            return {"status": "expired"}
        if new.booked_application_external_id:
            return {"status": "taken"}
        # Both rows change inside the same transaction: never two slots, never none.
        old.booked_application_external_id = ""
        new.booked_application_external_id = owner
        old.save(update_fields=["booked_application_external_id"])
        new.save(update_fields=["booked_application_external_id"])
        return {"status": "rescheduled", "previous_slot_id": str(old.id), **booking_data(new)}

    @staticmethod
    def _slot(tenant: Tenant, slot_id: object) -> CalendarSlot:
        try:
            return (
                CalendarSlot.objects.select_for_update()
                .select_related("site")
                .get(tenant=tenant, pk=str(slot_id))
            )
        except (CalendarSlot.DoesNotExist, ValueError, ValidationError) as exc:
            raise NotFound("Hueco no encontrado") from exc
