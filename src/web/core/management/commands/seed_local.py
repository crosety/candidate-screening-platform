import json
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils import timezone

from adapters.persistence.profiles import DjangoProfileRepository
from screening.application.ports import NewApplication
from screening.domain.profile import ProfileContent
from screening.platform.container import get_container
from web.accounts.models import User
from web.conversations.links import visible_token
from web.conversations.models import Application, CandidateLink
from web.simulated.models import AtsRecord
from web.simulated.seed import ensure_slots, load_places, load_sites
from web.tenancy.context import tenant_context
from web.tenancy.models import Membership, Tenant


class Command(BaseCommand):
    help = "Carga o actualiza los datos iniciales de la instalación local."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--renew-test-links", action="store_true")

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.SEED_PASSWORD:
            raise CommandError("Falta SEED_PASSWORD en .env")
        tenants = json.loads((settings.SEEDS_DIR / "tenants.json").read_text(encoding="utf-8"))
        users = json.loads((settings.SEEDS_DIR / "users.json").read_text(encoding="utf-8"))
        with transaction.atomic():
            for item in tenants:
                Tenant.objects.update_or_create(
                    slug=item["slug"],
                    defaults={
                        "name": item["name"],
                        "client_approval_required": item["client_approval_required"],
                    },
                )
            for item in users:
                user, _ = User.objects.update_or_create(
                    email=item["email"].lower(), defaults={"full_name": item["full_name"]}
                )
                user.set_password(settings.SEED_PASSWORD)
                user.save(update_fields=["password"])
                for membership in item["memberships"]:
                    tenant = Tenant.objects.get(slug=membership["tenant"])
                    Membership.objects.update_or_create(
                        user=user,
                        tenant=tenant,
                        defaults={
                            "role": membership["role"],
                            "allowed_segments": membership.get("allowed_segments", ["*"]),
                        },
                    )
            self.seed_profiles()
            self.seed_simulated(options.get("renew_test_links", False))
        self.stdout.write(
            f"{Tenant.objects.count()} clientes, {User.objects.count()} usuarios, "
            f"{Membership.objects.count()} membresías"
        )

    def seed_profiles(self) -> None:
        repo = DjangoProfileRepository()
        publisher = User.objects.get(email="fde@orbio.local")
        for tenant in Tenant.objects.all():
            path = settings.SEEDS_DIR / "tenants" / tenant.slug / "profile.json"
            if not path.exists() or repo.list_versions(tenant.id):
                continue
            content = ProfileContent.model_validate_json(path.read_text(encoding="utf-8"))
            version = repo.create_version(
                tenant.id, content, "medium", "Versión inicial", publisher.id
            )
            publication = repo.create_publication(
                tenant.id, version.id, None, 100, False, False, "Versión inicial", publisher.id
            )
            repo.activate(tenant.id, publication.id)
            repo.replace_draft(tenant.id, content, version.id, publisher.id)

    def seed_simulated(self, renew_test_links: bool = False) -> None:
        for tenant in Tenant.objects.all():
            directory = settings.SEEDS_DIR / "tenants" / tenant.slug
            if (directory / "sites.csv").exists():
                load_sites(tenant, directory / "sites.csv")
            if (directory / "places.csv").exists():
                load_places(tenant, directory / "places.csv")
            ensure_slots(tenant, timezone.now())
            applications = directory / "applications.json"
            if not applications.exists():
                continue
            for item in json.loads(applications.read_text(encoding="utf-8")):
                values = {key: item[key] for key in ("candidate_name", "phone", "city", "job_ref")}
                with tenant_context(tenant.id):
                    AtsRecord.objects.update_or_create(
                        tenant=tenant,
                        external_id=item["external_id"],
                        defaults={**values, "stage": "Nuevo"},
                    )
                if renew_test_links:
                    with tenant_context(tenant.id):
                        app = Application.objects.filter(
                            tenant=tenant,
                            external_id=item["external_id"],
                            is_test=True,
                            erased_at__isnull=True,
                        ).first()
                        old = (
                            CandidateLink.objects.filter(
                                tenant=tenant, application_id=app.id
                            ).first()
                            if app
                            else None
                        )
                        if old is not None and visible_token(old) is None:
                            call_command(
                                "candidate_link",
                                tenant=tenant.slug,
                                application=old.application_id,
                                action="rotate",
                                confirm=True,
                            )
                _, link = async_to_sync(get_container().conversations.ingest)(
                    tenant.slug, NewApplication(**item, is_test=True)
                )
                self.stdout.write(f"Enlace de {item['candidate_name']}: {link}")
