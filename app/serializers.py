from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (Appointment, AuditLog, Client, Comment, Company, Membership,
                     Resource, Role, Service, StatusDefinition, Task, TimeOff, WorkSchedule)


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = "__all__"


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        exclude = ["company"]


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
    settings = serializers.DictField()
    enabled_modules = serializers.ListField(child=serializers.CharField())
    available_modules = serializers.ListField(child=serializers.CharField(), read_only=True)


class MembershipSerializer(serializers.ModelSerializer):
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


class ResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resource
        exclude = ["company"]


class StatusDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = StatusDefinition
        exclude = ["company"]


class CleanModelSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance or self.Meta.model()
        for key, value in attrs.items():
            if not instance._meta.get_field(key).many_to_many:
                setattr(instance, key, value)
        if hasattr(self.context.get("view"), "get_company"):
            instance.company = self.context["view"].get_company()
        instance.clean()
        return attrs


class WorkScheduleSerializer(CleanModelSerializer):
    class Meta:
        model = WorkSchedule
        exclude = ["company"]


class TimeOffSerializer(CleanModelSerializer):
    class Meta:
        model = TimeOff
        exclude = ["company"]


class AppointmentSerializer(CleanModelSerializer):
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
        resources = attrs.get("resources", [])
        employee = attrs.get("employee", getattr(self.instance, "employee", None))
        company = self.context["view"].get_company()
        if starts_at and ends_at and employee:
            local_start = timezone.localtime(starts_at)
            local_end = timezone.localtime(ends_at)
            schedule_exists = WorkSchedule.objects.filter(
                company=company,
                employee=employee,
                weekday=local_start.weekday(),
                is_active=True,
                start_time__lte=local_start.time().replace(tzinfo=None),
                end_time__gte=local_end.time().replace(tzinfo=None),
            ).filter(
                models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=local_start.date())
            ).filter(
                models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=local_start.date())
            ).exists()
            if local_start.date() != local_end.date() or not schedule_exists:
                raise serializers.ValidationError({"starts_at": "Время находится вне рабочего графика сотрудника."})
            if TimeOff.objects.filter(
                company=company, employee=employee, is_approved=True,
                starts_at__lt=ends_at, ends_at__gt=starts_at,
            ).exists():
                raise serializers.ValidationError({"starts_at": "На это время у сотрудника запланировано отсутствие."})
        if starts_at and ends_at and resources:
            overlap = Appointment.objects.filter(company=self.context["view"].get_company(), resources__in=resources, starts_at__lt=ends_at, ends_at__gt=starts_at, cancelled_at__isnull=True)
            if self.instance:
                overlap = overlap.exclude(pk=self.instance.pk)
            if overlap.exists():
                raise serializers.ValidationError({"resources": "Один из ресурсов уже занят в это время."})
        return attrs


class TaskSerializer(CleanModelSerializer):
    class Meta:
        model = Task
        exclude = ["company", "created_by"]

    def validate_assignees(self, assignees):
        company = self.context["view"].get_company()
        if any(item.company_id != company.id for item in assignees):
            raise serializers.ValidationError("Исполнители должны быть из текущей компании.")
        return assignees


class CommentSerializer(CleanModelSerializer):
    author_detail = UserSummarySerializer(source="author", read_only=True)

    class Meta:
        model = Comment
        exclude = ["company", "author"]


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

    def validate_roles(self, roles):
        company = self.context["view"].get_company()
        if any(role.company_id != company.id for role in roles):
            raise serializers.ValidationError("Роль принадлежит другой компании.")
        return roles

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
