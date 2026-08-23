import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Идемпотентно создаёт первоначальную компанию и владельца из environment variables"

    def handle(self, *args, **options):
        config = {
            "company": os.getenv("BOOTSTRAP_COMPANY_NAME", "Fundament"),
            "slug": os.getenv("BOOTSTRAP_COMPANY_SLUG", "fundament"),
            "username": os.getenv("BOOTSTRAP_ADMIN_USERNAME", "owner"),
            "email": os.getenv("BOOTSTRAP_ADMIN_EMAIL", ""),
            "password": os.getenv("BOOTSTRAP_ADMIN_PASSWORD", ""),
        }
        if not config["password"]:
            raise CommandError("BOOTSTRAP_ADMIN_PASSWORD is required.")
        call_command("bootstrap_company", **config)
