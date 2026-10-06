from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
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
        if created:
            user.set_password(options["password"])
            user.is_staff = True
            user.is_superuser = True
            user.save()
        owner, _ = Role.objects.get_or_create(company=company, code="owner", defaults={"name": "Владелец", "permissions": ["*"], "is_system": True})
        role_defaults = {
            "manager": ("Менеджер", ["dashboard.view", "dashboard.view_all", "clients.view", "clients.view_all", "clients.manage", "employees.view", "employees.view_all", "employees.manage", "services.view", "services.manage", "resources.view", "resources.manage", "schedule.view", "schedule.view_all", "schedule.manage", "appointments.view", "appointments.view_all", "appointments.manage", "tasks.view", "tasks.view_all", "tasks.manage", "roles.view", "audit.view", "settings.view"]),
            "employee": ("Сотрудник", ["dashboard.view", "clients.view", "clients.manage", "services.view", "resources.view", "schedule.view", "appointments.view", "appointments.manage", "tasks.view", "tasks.manage"]),
            "viewer": ("Наблюдатель", ["dashboard.view", "clients.view", "clients.view_all", "employees.view", "employees.view_all", "services.view", "resources.view", "schedule.view", "schedule.view_all", "appointments.view", "appointments.view_all", "tasks.view", "tasks.view_all"]),
        }
        for code, (name, permissions) in role_defaults.items():
            Role.objects.get_or_create(company=company, code=code, defaults={"name": name, "permissions": permissions, "is_system": True})
        membership, membership_created = Membership.objects.get_or_create(user=user, company=company)
        if membership_created:
            membership.roles.add(owner)
        # New memberships receive their default schedule through post_save.
        # Existing (possibly split or intentionally empty) schedules are preserved.
        statuses = [
            ("appointment", "planned", "Запланирована", False, True),
            ("appointment", "completed", "Завершена", True, False),
            ("appointment", "cancelled", "Отменена", True, False),
            ("task", "new", "Новая", False, True),
            ("task", "in-progress", "В работе", False, False),
            ("task", "done", "Готово", True, False),
        ]
        for entity, code, name, closed, default in statuses:
            has_default = StatusDefinition.objects.filter(company=company, entity_type=entity, is_default=True).exists()
            StatusDefinition.objects.get_or_create(company=company, entity_type=entity, code=code, defaults={"name": name, "is_closed": closed, "is_default": default and not has_default})
        self.stdout.write(self.style.SUCCESS(f"Компания {company} готова. Админка: /admin/, API: /api/docs/"))
