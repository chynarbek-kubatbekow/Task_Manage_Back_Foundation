import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Max, Q


class TimestampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Company(TimestampedModel):
    AVAILABLE_MODULES = ["clients", "employees", "services", "resources", "schedule", "appointments", "tasks", "comments", "audit"]
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    timezone = models.CharField(max_length=64, default="Asia/Almaty")
    is_active = models.BooleanField(default=True)
    settings = models.JSONField(default=dict, blank=True, help_text="Брендинг и переключатели функций")

    class Meta:
        verbose_name = "Компания"
        verbose_name_plural = "Компании"

    def __str__(self):
        return self.name

    def module_enabled(self, module):
        enabled = self.settings.get("enabled_modules")
        return module in (enabled if enabled is not None else self.AVAILABLE_MODULES)

    def clean(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValidationError({"timezone": "Укажите существующий часовой пояс IANA."})
        if not isinstance(self.settings, dict):
            raise ValidationError({"settings": "Настройки должны быть JSON-объектом."})
        enabled = self.settings.get("enabled_modules")
        if enabled is not None and (not isinstance(enabled, list) or any(module not in self.AVAILABLE_MODULES for module in enabled)):
            raise ValidationError({"settings": "enabled_modules должен содержать список известных модулей."})


class Role(TimestampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=100)
    permissions = models.JSONField(default=list, blank=True, help_text="Например: clients.view, tasks.manage, schedule.edit")
    is_system = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Роль"
        verbose_name_plural = "Роли"
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_company_role")]

    def __str__(self):
        return f"{self.name} — {self.company}"

    def clean(self):
        if not isinstance(self.permissions, list) or any(not isinstance(value,str) for value in self.permissions):
            raise ValidationError({"permissions": "Права должны быть списком строк."})


class Membership(TimestampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    roles = models.ManyToManyField(Role, blank=True, related_name="memberships")
    job_title = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    color = models.CharField(max_length=16, default="#64748b")
    is_active = models.BooleanField(default=True)
    extra_data = models.JSONField(default=dict, blank=True)
    employee_number = models.PositiveIntegerField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Сотрудник компании"
        verbose_name_plural = "Сотрудники компаний"
        constraints = [
            models.UniqueConstraint(fields=["user", "company"], name="unique_company_member"),
            models.UniqueConstraint(fields=["company", "employee_number"], name="unique_company_employee_number"),
        ]

    def save(self, *args, **kwargs):
        if self.employee_number is None and self.company_id:
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=self.company_id)
                current = Membership.objects.filter(company_id=self.company_id).aggregate(value=Max("employee_number"))["value"] or 0
                self.employee_number = current + 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def has_permission(self, permission):
        if not self.is_active or not self.user.is_active or not self.company.is_active:
            return False
        if self.user.is_superuser:
            return True
        return any("*" in role.permissions or permission in role.permissions for role in self.roles.all())

    def __str__(self):
        return f"{self.user} — {self.company}"


class CompanyOwnedModel(TimestampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)

    class Meta:
        abstract = True


class Client(CompanyOwnedModel):
    patient_number = models.PositiveIntegerField(null=True, blank=True, editable=False)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    patronymic = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    birth_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    doctor_notes = models.TextField(blank=True)
    primary_doctor = models.ForeignKey(Membership, null=True, blank=True, on_delete=models.SET_NULL, related_name="primary_clients")
    tags = models.JSONField(default=list, blank=True)
    extra_data = models.JSONField(default=dict, blank=True, help_text="Настраиваемые поля клиента")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"
        ordering = ["patient_number", "last_name", "first_name"]
        indexes = [models.Index(fields=["company", "phone"]), models.Index(fields=["company", "email"])]
        constraints = [models.UniqueConstraint(fields=["company", "patient_number"], name="unique_company_patient_number")]

    def clean(self):
        if self.primary_doctor_id and self.primary_doctor.company_id != self.company_id:
            raise ValidationError("Лечащий врач должен принадлежать той же компании.")

    def save(self, *args, **kwargs):
        if self.patient_number is None and self.company_id:
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=self.company_id)
                current = Client.objects.filter(company_id=self.company_id).aggregate(value=Max("patient_number"))["value"] or 0
                self.patient_number = current + 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()


class Service(CompanyOwnedModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveIntegerField(default=60)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    category = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Услуга"
        verbose_name_plural = "Услуги"
        ordering = ["category", "name"]

    def __str__(self): return self.name

    def clean(self):
        if self.duration_minutes is not None and not 5 <= self.duration_minutes <= 480:
            raise ValidationError({"duration_minutes": "Длительность должна быть от 5 до 480 минут."})
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": "Стоимость не может быть отрицательной."})


class Resource(CompanyOwnedModel):
    name = models.CharField(max_length=255)
    resource_type = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Ресурс"
        verbose_name_plural = "Ресурсы"
        ordering = ["name"]

    def __str__(self): return self.name


class StatusDefinition(CompanyOwnedModel):
    class EntityType(models.TextChoices):
        APPOINTMENT = "appointment", "Запись"
        TASK = "task", "Задача"
        CLIENT = "client", "Клиент"

    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=100)
    color = models.CharField(max_length=16, default="#64748b")
    position = models.PositiveIntegerField(default=0)
    is_closed = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Статус"
        verbose_name_plural = "Статусы"
        ordering = ["entity_type", "position", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "entity_type", "code"], name="unique_entity_status")]

    def __str__(self):
        return f"{self.get_entity_type_display()}: {self.name}"

    def save(self, *args, **kwargs):
        if self.is_default and self.company_id:
            with transaction.atomic():
                Company.objects.select_for_update().get(pk=self.company_id)
                StatusDefinition.objects.filter(company_id=self.company_id, entity_type=self.entity_type, is_default=True).exclude(pk=self.pk).update(is_default=False)
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)


class WorkSchedule(CompanyOwnedModel):
    employee = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="work_schedules")
    weekday = models.PositiveSmallIntegerField(help_text="0=понедельник, 6=воскресенье")
    start_time = models.TimeField()
    end_time = models.TimeField()
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Рабочий график"
        verbose_name_plural = "Рабочие графики"
        ordering = ["employee", "weekday", "start_time"]
        constraints = [models.CheckConstraint(condition=Q(weekday__gte=0, weekday__lte=6), name="valid_weekday")]

    def clean(self):
        if self.employee_id and self.company_id != self.employee.company_id:
            raise ValidationError("Сотрудник должен принадлежать той же компании.")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError("Время окончания должно быть позже начала.")
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValidationError("Дата начала периода позже даты окончания.")

    def __str__(self):
        return f"{self.employee}: {self.weekday} {self.start_time}–{self.end_time}"


class TimeOff(CompanyOwnedModel):
    employee = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="time_off")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=255, blank=True)
    is_approved = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Нерабочее время"
        verbose_name_plural = "Нерабочее время"
        ordering = ["-starts_at"]

    def clean(self):
        if self.employee_id and self.company_id != self.employee.company_id:
            raise ValidationError("Сотрудник должен принадлежать той же компании.")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            raise ValidationError("Окончание должно быть позже начала.")


class Appointment(CompanyOwnedModel):
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="appointments")
    employee = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="appointments")
    service = models.ForeignKey(Service, null=True, blank=True, on_delete=models.PROTECT, related_name="appointments")
    resources = models.ManyToManyField(Resource, blank=True, related_name="appointments")
    status = models.ForeignKey(StatusDefinition, on_delete=models.PROTECT, related_name="appointments")
    title = models.CharField(max_length=255)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    notes = models.TextField(blank=True)
    extra_data = models.JSONField(default=dict, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    rescheduled_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="rescheduled_to")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_appointments")

    class Meta:
        verbose_name = "Запись"
        verbose_name_plural = "Записи"
        ordering = ["starts_at"]
        indexes = [models.Index(fields=["company", "starts_at"]), models.Index(fields=["employee", "starts_at", "ends_at"])]

    def clean(self):
        if not self.starts_at or not self.ends_at or not self.client_id or not self.employee_id or not self.status_id:
            return
        if self.starts_at >= self.ends_at:
            raise ValidationError("Окончание записи должно быть позже начала.")
        for obj, label in ((self.client, "Клиент"), (self.employee, "Сотрудник"), (self.status, "Статус"), (self.service, "Услуга")):
            if obj and obj.pk and obj.company_id != self.company_id:
                raise ValidationError(f"{label} принадлежит другой компании.")
        if self.status.entity_type != StatusDefinition.EntityType.APPOINTMENT:
            raise ValidationError("Выбран статус не для записей.")
        overlap = Appointment.objects.filter(company=self.company, employee=self.employee, starts_at__lt=self.ends_at, ends_at__gt=self.starts_at, cancelled_at__isnull=True)
        if self.pk:
            overlap = overlap.exclude(pk=self.pk)
        if not self.cancelled_at and overlap.exists():
            raise ValidationError("У сотрудника уже есть запись в это время.")

    def __str__(self):
        return f"{self.title} — {self.starts_at:%d.%m.%Y %H:%M}"


class Task(CompanyOwnedModel):
    class Priority(models.TextChoices):
        LOW = "low", "Низкий"
        NORMAL = "normal", "Обычный"
        HIGH = "high", "Высокий"
        URGENT = "urgent", "Срочный"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.ForeignKey(StatusDefinition, on_delete=models.PROTECT, related_name="tasks")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    assignees = models.ManyToManyField(Membership, blank=True, related_name="tasks")
    client = models.ForeignKey(Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    appointment = models.ForeignKey(Appointment, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="subtasks")
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_tasks")
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачи"
        ordering = ["completed_at", "due_at", "-created_at"]
        indexes = [models.Index(fields=["company", "due_at"])]

    def clean(self):
        if self.status_id and (self.status.company_id != self.company_id or self.status.entity_type != StatusDefinition.EntityType.TASK):
            raise ValidationError("Выбран некорректный статус задачи.")
        for obj, label in ((self.client, "Клиент"), (self.appointment, "Запись"), (self.parent, "Родительская задача")):
            if obj and obj.company_id != self.company_id:
                raise ValidationError(f"{label} принадлежит другой компании.")
        parent, visited = self.parent, {self.pk}
        while parent:
            if parent.pk in visited:
                raise ValidationError({"parent": "Задача не может быть родителем самой себя или образовывать цикл."})
            visited.add(parent.pk)
            parent = parent.parent

    def __str__(self):
        return self.title


class Comment(CompanyOwnedModel):
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.CASCADE, related_name="comments")
    appointment = models.ForeignKey(Appointment, null=True, blank=True, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="work_comments")
    text = models.TextField()

    class Meta:
        verbose_name = "Комментарий"
        verbose_name_plural = "Комментарии"
        ordering = ["created_at"]
        constraints = [models.CheckConstraint(condition=(Q(task__isnull=False, appointment__isnull=True) | Q(task__isnull=True, appointment__isnull=False)), name="comment_exactly_one_target")]

    def clean(self):
        if bool(self.task_id) == bool(self.appointment_id):
            raise ValidationError("Укажите ровно одну цель комментария: задачу или запись.")
        target = self.task or self.appointment
        if target and target.company_id != self.company_id:
            raise ValidationError("Объект комментария принадлежит другой компании.")


class AuditLog(TimestampedModel):
    company = models.ForeignKey(Company, null=True, on_delete=models.SET_NULL, related_name="audit_logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="audit_logs")
    action = models.CharField(max_length=20)
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    request_id = models.UUIDField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name = "Журнал изменений"
        verbose_name_plural = "Журнал изменений"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "created_at"]), models.Index(fields=["model_name", "object_id"])]

    def __str__(self):
        return f"{self.action} {self.model_name} {self.object_id}"
