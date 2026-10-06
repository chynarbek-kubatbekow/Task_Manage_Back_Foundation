from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import (Appointment, AuditLog, Client, Comment, Company, Membership,
                     Resource, Role, Service, StatusDefinition, Task, TimeOff, WorkSchedule)


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = "__all__"


class TenantUniqueMixin:
    """Company is injected at save time, so DRF cannot validate these keys itself."""
    def validate(self, attrs):
        attrs = super().validate(attrs)
        values = {name: attrs.get(name, getattr(self.instance, name, None)) for name in self.tenant_unique_fields}
        if all(value is not None for value in values.values()):
            queryset = self.Meta.model.objects.filter(company=self.context["view"].get_company(), **values)
            if self.instance:
                queryset = queryset.exclude(pk=self.instance.pk)
            if queryset.exists():
                raise serializers.ValidationError({self.tenant_unique_fields[-1]: "Такое значение уже используется в этой компании."})
        return attrs


class RoleSerializer(TenantUniqueMixin, serializers.ModelSerializer):
    tenant_unique_fields = ("code",)
    class Meta:
        model = Role
        exclude = ["company"]

    def validate_permissions(self, value):
        if not isinstance(value, list) or any(not isinstance(item,str) for item in value):
            raise serializers.ValidationError("Права должны быть списком строк.")
        return list(dict.fromkeys(value))


class UserSummarySerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "name", "is_active"]

    def get_name(self, obj) -> str:
        return obj.get_full_name() or obj.username


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()


class DashboardSerializer(serializers.Serializer):
    date = serializers.DateField()
    appointments_today = serializers.IntegerField()
    upcoming_24h = AppointmentSerializer(many=True, read_only=True) if "AppointmentSerializer" in globals() else serializers.ListField()
    open_tasks = serializers.IntegerField()
    overdue_tasks = serializers.IntegerField()
    active_clients = serializers.IntegerField()
    working_employees = serializers.IntegerField()


class MeSerializer(serializers.Serializer):
    user = UserSummarySerializer()
    active_company = serializers.DictField(allow_null=True)
    companies = serializers.ListField(child=serializers.DictField())
    permissions = serializers.ListField(child=serializers.CharField())
    capabilities = serializers.DictField()
    navigation = serializers.ListField(child=serializers.DictField())


class CompanySettingsSerializer(serializers.Serializer):
    settings = serializers.DictField(required=False)
    enabled_modules = serializers.ListField(child=serializers.ChoiceField(choices=Company.AVAILABLE_MODULES), required=False)
    available_modules = serializers.ListField(child=serializers.CharField(), read_only=True)

    def validate_settings(self, value):
        # Frontend sends the existing settings object together with the top-level flags.
        # The top-level field remains authoritative; discard its old nested copy.
        return {key: item for key, item in value.items() if key != "enabled_modules"}

    def validate_enabled_modules(self, value):
        return list(dict.fromkeys(value))


class MembershipSerializer(TenantUniqueMixin, serializers.ModelSerializer):
    tenant_unique_fields = ("user",)
    user_detail = UserSummarySerializer(source="user", read_only=True)

    class Meta:
        model = Membership
        exclude = ["company"]

    def validate_roles(self, roles):
        company = self.context["view"].get_company()
        if any(role.company_id != company.id for role in roles):
            raise serializers.ValidationError("Все роли должны принадлежать текущей компании.")
        return roles


class ClientSerializer(serializers.ModelSerializer):
    primary_doctor_detail = MembershipSerializer(source="primary_doctor", read_only=True)

    class Meta:
        model = Client
        exclude = ["company"]

    def validate_primary_doctor(self, doctor):
        if doctor and doctor.company_id != self.context["view"].get_company().id:
            raise serializers.ValidationError("Лечащий врач принадлежит другой компании.")
        return doctor


class PublicBookingClientSerializer(serializers.ModelSerializer):
    """Deliberately excludes all clinical and internal staff fields.

    Reserved for the future patient-facing booking API; it is not wired to a
    public endpoint yet.
    """

    class Meta:
        model = Client
        fields = ["first_name", "last_name", "patronymic", "phone", "email", "birth_date"]


class ServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        exclude = ["company"]

    def validate_duration_minutes(self, value):
        if not 5 <= value <= 480:
            raise serializers.ValidationError("Длительность должна быть от 5 до 480 минут.")
        return value

    def validate_price(self, value):
        if value < 0:
            raise serializers.ValidationError("Стоимость не может быть отрицательной.")
        return value


class ResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resource
        exclude = ["company"]


class StatusDefinitionSerializer(TenantUniqueMixin, serializers.ModelSerializer):
    tenant_unique_fields = ("entity_type", "code")
    class Meta:
        model = StatusDefinition
        exclude = ["company"]


class CleanModelSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance or self.Meta.model()
        view = self.context.get("view")
        if "employee" in attrs and hasattr(view, "can_view_all"):
            scope = "appointments" if self.Meta.model is Appointment else "schedule"
            if not view.can_view_all(scope):
                attrs["employee"] = view.request.membership
        for key, value in attrs.items():
            if not instance._meta.get_field(key).many_to_many:
                setattr(instance, key, value)
        if hasattr(self.context.get("view"), "get_company"):
            instance.company = self.context["view"].get_company()
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(getattr(error, "message_dict", None) or error.messages)
        return attrs


class WorkScheduleSerializer(CleanModelSerializer):
    class Meta:
        model = WorkSchedule
        exclude = ["company"]


class TimeOffSerializer(CleanModelSerializer):
    class Meta:
        model = TimeOff
        exclude = ["company"]


class AppointmentClientBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "patient_number", "first_name", "last_name", "patronymic"]


class EmployeeBriefSerializer(serializers.ModelSerializer):
    user_detail = UserSummarySerializer(source="user", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "employee_number", "job_title", "color", "user_detail"]


class ServiceBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Service
        fields = ["id", "name", "duration_minutes", "price"]


class AppointmentSerializer(CleanModelSerializer):
    client_detail = AppointmentClientBriefSerializer(source="client", read_only=True)
    employee_detail = EmployeeBriefSerializer(source="employee", read_only=True)
    service_detail = ServiceBriefSerializer(source="service", read_only=True)
    status_detail = StatusDefinitionSerializer(source="status", read_only=True)
    class Meta:
        model = Appointment
        exclude = ["company", "created_by"]
        read_only_fields = ["cancelled_at", "rescheduled_from"]

    def validate_resources(self, resources):
        company = self.context["view"].get_company()
        if any(resource.company_id != company.id for resource in resources):
            raise serializers.ValidationError("Ресурс принадлежит другой компании.")
        return resources

    def validate(self, attrs):
        attrs = super().validate(attrs)
        starts_at = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends_at = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        resources = attrs.get("resources", list(self.instance.resources.all()) if self.instance else [])
        employee = attrs.get("employee", getattr(self.instance, "employee", None))
        company = self.context["view"].get_company()
        if not self.instance and starts_at <= timezone.now():
            raise serializers.ValidationError({"starts_at": "Выберите время в будущем."})
        if not self.instance:
            for field in ("client", "employee", "service"):
                item = attrs.get(field)
                if item and not item.is_active:
                    raise serializers.ValidationError({field: "Объект находится в архиве."})
        if any(not resource.is_active for resource in resources):
            raise serializers.ValidationError({"resources": "Один из ресурсов находится в архиве."})
        if starts_at and ends_at and employee and not (self.instance and self.instance.cancelled_at):
            from .booking import validate_window
            try:
                validate_window(company, employee, starts_at, ends_at, resources, self.instance.pk if self.instance else None)
            except DjangoValidationError as error:
                raise serializers.ValidationError(error.message_dict)
        return attrs


class TaskSerializer(CleanModelSerializer):
    status_detail = StatusDefinitionSerializer(source="status", read_only=True)
    class Meta:
        model = Task
        exclude = ["company", "created_by"]

    def validate_assignees(self, assignees):
        company = self.context["view"].get_company()
        if any(item.company_id != company.id for item in assignees):
            raise serializers.ValidationError("Исполнители должны быть из текущей компании.")
        return assignees

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "status" in attrs and "completed_at" not in attrs:
            attrs["completed_at"] = timezone.now() if attrs["status"].is_closed else None
        return attrs


class CommentSerializer(CleanModelSerializer):
    author_detail = UserSummarySerializer(source="author", read_only=True)

    class Meta:
        model = Comment
        exclude = ["company", "author"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        view = self.context["view"]
        task = attrs.get("task", getattr(self.instance,"task",None))
        appointment = attrs.get("appointment", getattr(self.instance,"appointment",None))
        if task and not view.has_product_permission("tasks.view_all") and not view.has_product_permission("tasks.manage_all") and not view.request.user.is_superuser:
            if not task.assignees.filter(pk=view.request.membership.pk).exists():
                raise serializers.ValidationError({"task": "Нет доступа к этой задаче."})
        if appointment and not view.has_product_permission("appointments.view_all") and not view.has_product_permission("appointments.manage_all") and not view.request.user.is_superuser:
            if appointment.employee_id != view.request.membership.pk:
                raise serializers.ValidationError({"appointment": "Нет доступа к этой записи."})
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    actor_detail = UserSummarySerializer(source="actor", read_only=True)

    class Meta:
        model = AuditLog
        fields = "__all__"
        read_only_fields = [field.name for field in AuditLog._meta.fields]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    role_ids = serializers.PrimaryKeyRelatedField(source="roles", many=True, queryset=Role.objects.all(), write_only=True, required=False)
    job_title = serializers.CharField(write_only=True, required=False, allow_blank=True)
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "email", "first_name", "last_name", "password", "role_ids", "job_title", "phone"]
        read_only_fields = ["id"]

    def validate_role_ids(self, roles):
        company = self.context["view"].get_company()
        if any(role.company_id != company.id for role in roles):
            raise serializers.ValidationError("Роль принадлежит другой компании.")
        return roles

    def validate(self, attrs):
        candidate = get_user_model()(**{key: attrs.get(key, "") for key in ("username", "email", "first_name", "last_name")})
        try:
            validate_password(attrs["password"], candidate)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": error.messages})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        roles = validated_data.pop("roles", [])
        job_title = validated_data.pop("job_title", "")
        phone = validated_data.pop("phone", "")
        password = validated_data.pop("password")
        user = get_user_model().objects.create_user(password=password, **validated_data)
        membership = Membership.objects.create(user=user, company=self.context["view"].get_company(), job_title=job_title, phone=phone)
        membership.roles.set(roles)
        return user
