import os

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Идемпотентно создаёт первоначальную компанию и владельца из environment variables"

    def handle(self, *args, **options):
        config = {
            "company": os.getenv("BOOTSTRAP_COMPANY_NAME", "").strip() or "Fundament",
            "slug": os.getenv("BOOTSTRAP_COMPANY_SLUG", "").strip() or "fundament",
            "username": os.getenv("BOOTSTRAP_ADMIN_USERNAME", "").strip() or "owner",
            "email": os.getenv("BOOTSTRAP_ADMIN_EMAIL", ""),
            "password": os.getenv("BOOTSTRAP_ADMIN_PASSWORD", ""),
        }
        if not config["password"]:
            self.stdout.write("Bootstrap skipped: BOOTSTRAP_ADMIN_PASSWORD is not set. Existing data is unchanged.")
            return
        call_command("bootstrap_company", **config)
