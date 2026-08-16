from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Company, Membership, Role, StatusDefinition


class Command(BaseCommand):
    help = "Создаёт компанию, владельца, базовые роли и статусы"

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True)
        parser.add_argument("--slug", required=True)
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--password", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        company, _ = Company.objects.get_or_create(slug=options["slug"], defaults={"name": options["company"]})
        user, created = get_user_model().objects.get_or_create(username=options["username"], defaults={"email": options["email"]})
        if not created and Membership.objects.filter(user=user, company=company).exists():
            raise CommandError("Такой пользователь уже состоит в компании.")
        user.email = options["email"] or user.email
        user.set_password(options["password"])
        user.is_staff = True
        user.save()
        owner, _ = Role.objects.get_or_create(company=company, code="owner", defaults={"name": "Владелец", "permissions": ["*"], "is_system": True})
        defaults = {
            "manager": ["dashboard.view", "dashboard.view_all", "clients.view", "clients.view_all", "clients.manage", "employees.view", "employees.view_all", "employees.manage", "services.view", "services.manage", "resources.view", "resources.manage", "schedule.view", "schedule.view_all", "schedule.manage", "appointments.view", "appointments.view_all", "appointments.manage", "tasks.view", "tasks.view_all", "tasks.manage", "roles.view", "audit.view", "settings.view"],
            "employee": ["dashboard.view", "clients.view", "services.view", "resources.view", "schedule.view", "appointments.view", "appointments.manage", "tasks.view", "tasks.manage"],
            "viewer": ["dashboard.view", "clients.view", "clients.view_all", "employees.view", "employees.view_all", "services.view", "resources.view", "schedule.view", "schedule.view_all", "appointments.view", "appointments.view_all", "tasks.view", "tasks.view_all"],
        }
        for code, permissions in defaults.items():
            Role.objects.update_or_create(company=company, code=code, defaults={"name": code.title(), "permissions": permissions, "is_system": True})
        membership, _ = Membership.objects.get_or_create(user=user, company=company)
        membership.roles.add(owner)
        statuses = [("appointment", "planned", "Запланирована", False, True), ("appointment", "completed", "Завершена", True, False), ("appointment", "cancelled", "Отменена", True, False), ("task", "new", "Новая", False, True), ("task", "in-progress", "В работе", False, False), ("task", "done", "Готово", True, False)]
        for entity, code, name, closed, default in statuses:
            StatusDefinition.objects.get_or_create(company=company, entity_type=entity, code=code, defaults={"name": name, "is_closed": closed, "is_default": default})
        self.stdout.write(self.style.SUCCESS(f"Компания {company} готова. Админка: /admin/, API: /api/docs/"))
