from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.db import connection, transaction
from django.http import HttpResponse
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from .models import (Appointment, AuditLog, Client, Comment, Company, Membership, Resource, Role,
                     Service, StatusDefinition, Task, TimeOff, WorkSchedule)
from .permissions import CompanyContextMixin, RolePermission
from .serializers import (AppointmentSerializer, AuditLogSerializer, ClientSerializer,
                          CommentSerializer, CompanySerializer, CompanySettingsSerializer, DashboardSerializer, HealthSerializer, MembershipSerializer, MeSerializer, ResourceSerializer, RoleSerializer, ServiceSerializer,
                          StatusDefinitionSerializer, TaskSerializer, TimeOffSerializer,
                          UserCreateSerializer, WorkScheduleSerializer)


class CompanyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Company.objects.order_by("name", "id")
    serializer_class = CompanySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        if self.request.user.is_superuser:
            return self.queryset
        return self.queryset.filter(memberships__user=self.request.user, memberships__is_active=True, is_active=True).distinct()


class StableOrderingFilter(filters.OrderingFilter):
    def get_ordering(self, request, queryset, view):
        ordering = super().get_ordering(request, queryset, view)
        if ordering:
            return [*ordering, *([] if any(value.lstrip("-") in {"id", "pk"} for value in ordering) else ["id"])]
        return ordering


class TenantModelViewSet(CompanyContextMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, RolePermission]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, StableOrderingFilter]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        qs = self.queryset.filter(company=self.get_company())
        ordering = qs.query.order_by or qs.model._meta.ordering or ["created_at"]
        qs = qs.order_by(*ordering, *([] if any(value.lstrip("-") in {"id","pk"} for value in ordering) else ["id"]))
        if hasattr(self.queryset.model, "is_active") and self.action not in {"restore", "hard_delete"} and self.request.query_params.get("include_inactive") != "true":
            qs = qs.filter(is_active=True)
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        extras = {"company": self.get_company()}
        if "created_by" in [f.name for f in serializer.Meta.model._meta.fields]:
            extras["created_by"] = self.request.user
        instance = serializer.save(**extras)
        self._audit("create", instance)

    @transaction.atomic
    def perform_update(self, serializer):
        instance = serializer.save()
        self._audit("update", instance)

    def perform_destroy(self, instance):
        if hasattr(instance, "is_active"):
            instance.is_active = False
            instance.save(update_fields=["is_active", "updated_at"])
            self._audit("archive", instance)
        else:
            self._audit("delete", instance)
            instance.delete()

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request,*args,**kwargs)
        except ProtectedError:
            transaction.set_rollback(True)
            return Response({"detail": "Объект используется в истории. Удаление невозможно."}, status=status.HTTP_409_CONFLICT)

    @action(detail=True, methods=["post"], url_path="restore")
    @transaction.atomic
    def restore(self, request, pk=None):
        instance = self.get_object()
        if not hasattr(instance, "is_active"):
            return Response({"detail": "Эта сущность не поддерживает архив."}, status=status.HTTP_400_BAD_REQUEST)
        instance.is_active = True
        instance.save(update_fields=["is_active", "updated_at"])
        self._audit("restore", instance)
        return Response(self.get_serializer(instance).data)

    @action(detail=True, methods=["delete"], url_path="hard-delete")
    @transaction.atomic
    def hard_delete(self, request, pk=None):
        instance = self.get_object()
        label = str(instance)
        try:
            self._audit("hard_delete", instance)
            instance.delete()
        except ProtectedError:
            transaction.set_rollback(True)
            return Response({"detail": "Объект связан с историческими данными. Сначала удалите зависимости либо используйте обычное архивирование."}, status=status.HTTP_409_CONFLICT)
        return Response({"detail": f"Удалено: {label}"}, status=status.HTTP_200_OK)

    def _audit(self, action_name, instance, changes=None):
        AuditLog.objects.create(company=self.get_company(), actor=self.request.user, action=action_name,
                                model_name=instance._meta.label_lower, object_id=str(instance.pk),
                                object_repr=str(instance)[:255], changes=changes or {},
                                ip_address=self.request.META.get("REMOTE_ADDR"))

    def can_view_all(self, resource):
        return self.has_product_permission(f"{resource}.view_all") or self.has_product_permission(f"{resource}.manage_all")


class RoleViewSet(TenantModelViewSet):
    module_key = "employees"
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    search_fields = ["name", "code"]
    permission_map = {"list": "roles.view", "retrieve": "roles.view", "create": "roles.manage", "update": "roles.manage", "partial_update": "roles.manage", "destroy": "roles.manage"}

    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError({"detail": "Системную роль можно настроить, но нельзя удалить."})
        super().perform_destroy(instance)


class MembershipViewSet(TenantModelViewSet):
    module_key = "employees"
    queryset = Membership.objects.select_related("user", "company").prefetch_related("roles").order_by("created_at")
    serializer_class = MembershipSerializer
    filterset_fields = ["is_active", "roles"]
    search_fields = ["user__username", "user__first_name", "user__last_name", "job_title", "phone"]
    permission_map = {"list": "employees.view", "retrieve": "employees.view", "create": "employees.manage", "update": "employees.manage", "partial_update": "employees.manage", "destroy": "employees.manage"}

    def get_queryset(self):
        qs = super().get_queryset()
        return qs if self.can_view_all("employees") else qs.filter(user=self.request.user)


class UserViewSet(CompanyContextMixin, viewsets.GenericViewSet):
    module_key = "employees"
    queryset = get_user_model().objects.all()
    serializer_class = UserCreateSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    permission_map = {"create": "employees.manage"}

    def create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ClientViewSet(TenantModelViewSet):
    module_key = "clients"
    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    filterset_fields = ["is_active"]
    search_fields = ["=patient_number", "first_name", "last_name", "patronymic", "phone", "email", "diagnosis", "doctor_notes", "notes"]
    ordering_fields = ["patient_number", "created_at", "updated_at", "first_name", "last_name"]
    permission_map = {"list": "clients.view", "retrieve": "clients.view", "create": "clients.manage", "update": "clients.manage", "partial_update": "clients.manage", "destroy": "clients.manage"}

    def get_queryset(self):
        qs = super().get_queryset()
        if self.can_view_all("clients") or self.has_product_permission("clients.manage"):
            return qs
        return qs.filter(appointments__employee=self.request.membership).distinct()


class ServiceViewSet(TenantModelViewSet):
    module_key = "services"
    queryset = Service.objects.all()
    serializer_class = ServiceSerializer
    filterset_fields = ["is_active", "category"]
    search_fields = ["name", "description", "category"]
    permission_map = {"list": "services.view", "retrieve": "services.view", "create": "services.manage", "update": "services.manage", "partial_update": "services.manage", "destroy": "services.manage"}


class ResourceViewSet(TenantModelViewSet):
    module_key = "resources"
    queryset = Resource.objects.all()
    serializer_class = ResourceSerializer
    filterset_fields = ["is_active", "resource_type"]
    search_fields = ["name", "resource_type"]
    permission_map = {"list": "resources.view", "retrieve": "resources.view", "create": "resources.manage", "update": "resources.manage", "partial_update": "resources.manage", "destroy": "resources.manage"}


class StatusViewSet(TenantModelViewSet):
    permission_namespace = "settings"
    queryset = StatusDefinition.objects.all()
    serializer_class = StatusDefinitionSerializer
    filterset_fields = ["entity_type", "is_closed", "is_default"]
    search_fields = ["name", "code"]
    permission_map = {"list": ["settings.view", "tasks.view", "appointments.view"], "retrieve": ["settings.view", "tasks.view", "appointments.view"], "create": "settings.manage", "update": "settings.manage", "partial_update": "settings.manage", "destroy": "settings.manage"}


class WorkScheduleViewSet(TenantModelViewSet):
    module_key = "schedule"
    queryset = WorkSchedule.objects.select_related("employee", "employee__user")
    serializer_class = WorkScheduleSerializer
    filterset_fields = ["employee", "weekday", "is_active"]
    permission_map = {"list": "schedule.view", "retrieve": "schedule.view", "create": "schedule.manage", "update": "schedule.manage", "partial_update": "schedule.manage", "destroy": "schedule.manage"}

    def get_queryset(self):
        qs = super().get_queryset()
        return qs if self.can_view_all("schedule") else qs.filter(employee=self.request.membership)

    @transaction.atomic
    def perform_create(self, serializer):
        employee = serializer.validated_data.get("employee") if self.can_view_all("schedule") else self.request.membership
        instance = serializer.save(company=self.get_company(), employee=employee)
        self._audit("create", instance)

    @transaction.atomic
    def perform_update(self, serializer):
        employee = serializer.validated_data.get("employee") if self.can_view_all("schedule") else self.request.membership
        instance = serializer.save(employee=employee)
        self._audit("update", instance)


class TimeOffViewSet(TenantModelViewSet):
    module_key = "schedule"
    queryset = TimeOff.objects.select_related("employee", "employee__user")
    serializer_class = TimeOffSerializer
    filterset_fields = ["employee", "is_approved"]
    ordering_fields = ["starts_at", "ends_at"]
    permission_map = WorkScheduleViewSet.permission_map

    def get_queryset(self):
        qs = super().get_queryset()
        return qs if self.can_view_all("schedule") else qs.filter(employee=self.request.membership)

    perform_create = WorkScheduleViewSet.perform_create
    perform_update = WorkScheduleViewSet.perform_update


class AppointmentViewSet(TenantModelViewSet):
    module_key = "appointments"
    queryset = Appointment.objects.select_related("client", "employee", "employee__user", "status", "service").prefetch_related("resources")
    serializer_class = AppointmentSerializer
    filterset_fields = ["employee", "client", "status"]
    search_fields = ["title", "client__first_name", "client__last_name", "client__phone", "notes"]
    ordering_fields = ["starts_at", "ends_at", "created_at"]
    permission_map = {"list": "appointments.view", "retrieve": "appointments.view", "create": "appointments.manage", "update": "appointments.manage", "partial_update": "appointments.manage", "destroy": "appointments.manage", "cancel": "appointments.manage", "reschedule": "appointments.manage", "available_slots": "appointments.view"}

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.can_view_all("appointments"):
            qs = qs.filter(employee=self.request.membership)
        start, end = self.request.query_params.get("start"), self.request.query_params.get("end")
        values = {}
        for name, value in (("start", start), ("end", end)):
            if value:
                try:
                    parsed = parse_datetime(value)
                except ValueError:
                    parsed = None
                if parsed is None:
                    raise ValidationError({name: "Укажите дату и время в формате ISO 8601."})
                values[name] = timezone.make_aware(parsed, ZoneInfo(self.get_company().timezone)) if timezone.is_naive(parsed) else parsed
        start, end = values.get("start"), values.get("end")
        if start and end and start >= end:
            raise ValidationError({"end": "Конец периода должен быть позже начала."})
        if start:
            qs = qs.filter(ends_at__gt=start)
        if end:
            qs = qs.filter(starts_at__lt=end)
        return qs

    def lock_booking(self, employee_ids, resources):
        # Consistent lock order avoids deadlocks between simultaneous bookings.
        list(Membership.objects.filter(pk__in=employee_ids).order_by("pk").select_for_update())
        list(Resource.objects.filter(pk__in=[item.pk for item in resources]).order_by("pk").select_for_update())

    @transaction.atomic
    def perform_create(self, serializer):
        employee = serializer.validated_data.get("employee") if self.can_view_all("appointments") else self.request.membership
        self.lock_booking([employee.pk], serializer.validated_data.get("resources", []))
        serializer.validate(serializer.validated_data)
        instance = serializer.save(company=self.get_company(), created_by=self.request.user, employee=employee)
        self._audit("create", instance)

    @transaction.atomic
    def perform_update(self, serializer):
        employee = serializer.validated_data.get("employee", serializer.instance.employee) if self.can_view_all("appointments") else self.request.membership
        resources = serializer.validated_data.get("resources", list(serializer.instance.resources.all()))
        previous = Appointment.objects.get(pk=serializer.instance.pk)
        self.lock_booking([employee.pk, previous.employee_id], resources)
        serializer.instance = Appointment.objects.select_for_update().get(pk=previous.pk)
        serializer.validate(serializer.validated_data)
        instance = serializer.save(employee=employee)
        self._audit("update", instance)

    @action(detail=False, methods=["get"], url_path="available-slots")
    def available_slots(self, request):
        """Return free start times for one employee on one local calendar day."""
        company = self.get_company()
        employee_id = request.query_params.get("employee")
        date_value = request.query_params.get("date")
        service_id = request.query_params.get("service")
        try:
            selected_date = datetime.strptime(date_value, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return Response({"date": "Используйте формат YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            employee = Membership.objects.select_related("user").get(id=employee_id, company=company, is_active=True)
        except (Membership.DoesNotExist, ValueError, DjangoValidationError):
            return Response({"employee": "Сотрудник не найден."}, status=status.HTTP_400_BAD_REQUEST)
        if not self.can_view_all("appointments") and employee.pk != self.request.membership.pk:
            return Response({"detail": "Доступно только собственное расписание."}, status=status.HTTP_403_FORBIDDEN)
        duration = 60
        if service_id:
            try:
                service = Service.objects.filter(id=service_id, company=company, is_active=True).first()
            except (ValueError, DjangoValidationError):
                service = None
            if not service:
                return Response({"service": "Услуга не найдена."}, status=status.HTTP_400_BAD_REQUEST)
            duration = service.duration_minutes
        elif request.query_params.get("duration"):
            try:
                duration = max(5, min(int(request.query_params["duration"]), 480))
            except ValueError:
                return Response({"duration": "Длительность должна быть числом."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            slot_step = max(1, min(int(request.query_params.get("step", 60)), 120))
        except ValueError:
            return Response({"step": "Шаг времени должен быть числом."}, status=status.HTTP_400_BAD_REQUEST)

        schedules = WorkSchedule.objects.filter(
            company=company, employee=employee, weekday=selected_date.weekday(), is_active=True
        ).filter(Q(valid_from__isnull=True) | Q(valid_from__lte=selected_date)).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=selected_date))
        company_zone = ZoneInfo(company.timezone)
        day_start = timezone.make_aware(datetime.combine(selected_date, datetime.min.time()), company_zone)
        day_end = day_start + timedelta(days=1)
        busy_query = Appointment.objects.filter(
            company=company, employee=employee, starts_at__lt=day_end,
            ends_at__gt=day_start, cancelled_at__isnull=True,
        )
        exclude_id = request.query_params.get("exclude")
        if exclude_id:
            try:
                from uuid import UUID
                UUID(exclude_id)
            except (ValueError, TypeError):
                raise ValidationError({"exclude": "Укажите корректный UUID записи."})
            busy_query = busy_query.exclude(id=exclude_id)
        busy = list(busy_query.values_list("starts_at", "ends_at"))
        time_off = list(TimeOff.objects.filter(
            company=company, employee=employee, is_approved=True,
            starts_at__lt=day_end, ends_at__gt=day_start,
        ).values_list("starts_at", "ends_at"))
        slots = []
        step = timedelta(minutes=slot_step)
        length = timedelta(minutes=duration)
        now = timezone.now()
        for schedule in schedules:
            cursor = timezone.make_aware(datetime.combine(selected_date, schedule.start_time), company_zone)
            finish = timezone.make_aware(datetime.combine(selected_date, schedule.end_time), company_zone)
            while cursor + length <= finish:
                end = cursor + length
                if cursor >= now and not any(start < end and stop > cursor for start, stop in busy + time_off):
                    slots.append({"starts_at": cursor.isoformat(), "ends_at": end.isoformat()})
                cursor += step
        return Response({
            "employee": str(employee.id),
            "employee_name": employee.user.get_full_name() or employee.user.username,
            "date": selected_date,
            "duration_minutes": duration,
            "step_minutes": slot_step,
            "slots": slots,
        })

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        obj = self.get_object()
        obj.cancelled_at = timezone.now()
        obj.cancellation_reason = request.data.get("reason", "")
        obj.save(update_fields=["cancelled_at", "cancellation_reason", "updated_at"])
        self._audit("cancel", obj, {"reason": obj.cancellation_reason})
        return Response(self.get_serializer(obj).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def reschedule(self, request, pk=None):
        original = self.get_object()
        # Lock the employee to serialize competing reschedules and re-read the record.
        employee_id = request.data.get("employee", original.employee_id) if self.can_view_all("appointments") else self.request.membership.pk
        try:
            target = Membership.objects.get(pk=employee_id, company=self.get_company(), is_active=True)
        except (Membership.DoesNotExist, ValueError, DjangoValidationError):
            raise ValidationError({"employee": "Сотрудник не найден."})
        submitted_resources = request.data.get("resources", list(original.resources.values_list("id", flat=True)))
        if not isinstance(submitted_resources, list):
            raise ValidationError({"resources": "Ожидается список UUID ресурсов."})
        try:
            from uuid import UUID
            resource_ids = [UUID(str(value)) for value in submitted_resources]
        except (ValueError, TypeError):
            raise ValidationError({"resources": "Укажите корректные UUID ресурсов."})
        resource_ids.extend(original.resources.values_list("id", flat=True))
        self.lock_booking([original.employee_id, target.pk], Resource.objects.filter(company=self.get_company(), id__in=resource_ids))
        original = self.get_queryset().select_related(None).select_for_update().get(pk=original.pk)
        if original.cancelled_at:
            return Response({"detail": "Отменённую запись нельзя перенести."}, status=status.HTTP_400_BAD_REQUEST)
        original.cancelled_at = timezone.now()
        original.cancellation_reason = "Перенесено"
        original.save(update_fields=["cancelled_at", "cancellation_reason", "updated_at"])
        # Cancellation and replacement are one transaction; failed validation rolls back.
        serializer = self.get_serializer(data={**request.data, "client": original.client_id, "employee": employee_id, "status": request.data.get("status", original.status_id), "title": request.data.get("title", original.title)})
        serializer.is_valid(raise_exception=True)
        new_obj = serializer.save(company=self.get_company(), created_by=request.user, rescheduled_from=original)
        self._audit("reschedule", new_obj, {"from": str(original.pk)})
        return Response(self.get_serializer(new_obj).data, status=status.HTTP_201_CREATED)


class TaskViewSet(TenantModelViewSet):
    module_key = "tasks"
    queryset = Task.objects.select_related("status", "client", "appointment", "created_by").prefetch_related("assignees")
    serializer_class = TaskSerializer
    filterset_fields = ["status", "priority", "assignees", "client", "appointment", "completed_at"]
    search_fields = ["title", "description"]
    ordering_fields = ["due_at", "created_at", "updated_at", "priority"]
    permission_map = {"list": "tasks.view", "retrieve": "tasks.view", "create": "tasks.manage", "update": "tasks.manage", "partial_update": "tasks.manage", "destroy": "tasks.manage"}

    def get_queryset(self):
        qs = super().get_queryset()
        return qs if self.can_view_all("tasks") else qs.filter(assignees=self.request.membership)

    @transaction.atomic
    def perform_create(self, serializer):
        instance = serializer.save(company=self.get_company(), created_by=self.request.user)
        if not self.can_view_all("tasks"):
            instance.assignees.set([self.request.membership])
        self._audit("create", instance)

    @transaction.atomic
    def perform_update(self, serializer):
        instance = serializer.save()
        if not self.can_view_all("tasks"):
            instance.assignees.set([self.request.membership])
        self._audit("update", instance)


class CommentViewSet(TenantModelViewSet):
    module_key = "comments"
    queryset = Comment.objects.select_related("author", "task", "appointment")
    serializer_class = CommentSerializer
    filterset_fields = ["task", "appointment", "author"]
    permission_map = {"list": ["tasks.view", "appointments.view"], "retrieve": ["tasks.view", "appointments.view"], "create": ["tasks.manage", "appointments.manage"], "update": ["tasks.manage", "appointments.manage"], "partial_update": ["tasks.manage", "appointments.manage"], "destroy": ["tasks.manage", "appointments.manage"]}

    @transaction.atomic
    def perform_create(self, serializer):
        instance = serializer.save(company=self.get_company(), author=self.request.user)
        self._audit("create", instance)

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_superuser:
            return qs
        task_scope = Q(task__isnull=False) if self.can_view_all("tasks") else Q(task__assignees=self.request.membership)
        appointment_scope = Q(appointment__isnull=False) if self.can_view_all("appointments") else Q(appointment__employee=self.request.membership)
        return qs.filter(task_scope | appointment_scope).distinct()


class AuditLogViewSet(CompanyContextMixin, viewsets.ReadOnlyModelViewSet):
    module_key = "audit"
    queryset = AuditLog.objects.all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, RolePermission]
    permission_map = {"list": "audit.view", "retrieve": "audit.view"}
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["action", "model_name", "actor"]
    search_fields = ["object_repr", "object_id"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        return AuditLog.objects.filter(company=self.get_company()).select_related("actor")


class DashboardView(CompanyContextMixin, APIView):
    permission_classes = [IsAuthenticated, RolePermission]
    action = "retrieve"
    permission_map = {"retrieve": "dashboard.view"}

    @extend_schema(responses=DashboardSerializer)
    def get(self, request):
        company = self.get_company()
        now = timezone.now()
        local_now = timezone.localtime(now, ZoneInfo(company.timezone))
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        appointments = Appointment.objects.filter(company=company, cancelled_at__isnull=True)
        tasks = Task.objects.filter(company=company, completed_at__isnull=True)
        if not self.has_product_permission("dashboard.view_all"):
            appointments = appointments.filter(employee=request.membership)
            tasks = tasks.filter(assignees=request.membership)
        all_scope = self.has_product_permission("dashboard.view_all")
        active_clients = Client.objects.filter(company=company, is_active=True)
        schedules = WorkSchedule.objects.filter(company=company, weekday=local_now.weekday(), is_active=True).filter(Q(valid_from__isnull=True) | Q(valid_from__lte=local_now.date())).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=local_now.date()))
        if not all_scope:
            active_clients = active_clients.filter(appointments__employee=request.membership).distinct()
            schedules = schedules.filter(employee=request.membership)
        return Response({"date": local_now.date(), "appointments_today": appointments.filter(starts_at__gte=day_start, starts_at__lt=day_end).count(), "upcoming_24h": AppointmentSerializer(appointments.filter(starts_at__gte=now, starts_at__lt=now + timedelta(days=1)).order_by("starts_at")[:20], many=True).data, "open_tasks": tasks.count(), "overdue_tasks": tasks.filter(due_at__lt=now).count(), "active_clients": active_clients.count(), "working_employees": schedules.values("employee").distinct().count()})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        memberships = Membership.objects.filter(user=request.user, is_active=True, company__is_active=True).select_related("company").prefetch_related("roles")
        companies = []
        for membership in memberships:
            companies.append({"id": str(membership.company_id), "name": membership.company.name, "slug": membership.company.slug, "timezone": membership.company.timezone, "roles": [{"id": str(role.id), "name": role.name, "code": role.code} for role in membership.roles.all()]})
        selected_id = request.headers.get("X-Company-ID") or request.query_params.get("company")
        selected = next((membership for membership in memberships if str(membership.company_id) == selected_id), None)
        selected_company = selected.company if selected else None
        if request.user.is_superuser:
            known_ids = {item["id"] for item in companies}
            for company in Company.objects.filter(is_active=True):
                if str(company.id) not in known_ids:
                    companies.append({"id": str(company.id), "name": company.name, "slug": company.slug, "timezone": company.timezone, "roles": [{"name": "Суперпользователь", "code": "superuser"}]})
                if str(company.id) == selected_id:
                    selected_company = company
        permissions = set()
        if request.user.is_superuser:
            permissions = {"*"}
        elif selected:
            for role in selected.roles.all():
                permissions.update(role.permissions)

        def can(*names):
            for name in names:
                module = name.split(".", 1)[0]
                if selected_company and module in Company.AVAILABLE_MODULES and not selected_company.module_enabled(module):
                    continue
                if "*" in permissions or name in permissions:
                    return True
            return False

        capability_names = [
            "dashboard.view", "clients.view", "clients.manage", "employees.view", "employees.manage",
            "services.view", "services.manage", "resources.view", "resources.manage", "schedule.view",
            "schedule.manage", "appointments.view", "appointments.manage", "tasks.view", "tasks.manage",
            "roles.view", "roles.manage", "settings.view", "settings.manage", "audit.view",
        ]
        capabilities = {name: can(name) for name in capability_names}
        navigation_config = [
            ("dashboard", "Главная", "dashboard.view"), ("calendar", "Расписание", "appointments.view"),
            ("tasks", "Задачи", "tasks.view"), ("clients", "Клиенты", "clients.view"),
            ("team", "Сотрудники", "employees.view"), ("services", "Услуги", "services.view"),
            ("resources", "Ресурсы", "resources.view"), ("settings", "Настройки", "settings.view"),
            ("audit", "История", "audit.view"),
        ]
        navigation = [{"key": key, "label": label} for key, label, permission in navigation_config if can(permission)]
        active_company = None
        if selected_company:
            active_company = {"id": str(selected_company.id), "name": selected_company.name, "slug": selected_company.slug, "timezone": selected_company.timezone, "settings": selected_company.settings, "membership_id": str(selected.id) if selected else None, "job_title": selected.job_title if selected else "Суперпользователь", "color": selected.color if selected else "#64748b"}
        from .serializers import UserSummarySerializer
        return Response({"user": UserSummarySerializer(request.user).data, "active_company": active_company, "companies": companies, "permissions": sorted(permissions), "capabilities": capabilities, "navigation": navigation})


class CompanySettingsView(CompanyContextMixin, APIView):
    permission_classes = [IsAuthenticated]

    def _allowed(self, permission):
        return self.request.user.is_superuser or self.has_product_permission(permission)

    @extend_schema(responses=CompanySettingsSerializer)
    def get(self, request):
        company = self.get_company()
        if not self._allowed("settings.view"):
            return Response({"detail": "Нет права settings.view."}, status=status.HTTP_403_FORBIDDEN)
        return Response({"settings": company.settings, "enabled_modules": company.settings.get("enabled_modules", Company.AVAILABLE_MODULES), "available_modules": Company.AVAILABLE_MODULES})

    @extend_schema(request=CompanySettingsSerializer, responses=CompanySettingsSerializer)
    def patch(self, request):
        company = self.get_company()
        if not self._allowed("settings.manage"):
            return Response({"detail": "Нет права settings.manage."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CompanySettingsSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        enabled = serializer.validated_data.get("enabled_modules", company.settings.get("enabled_modules", Company.AVAILABLE_MODULES))
        new_settings = {**company.settings, **serializer.validated_data.get("settings", {}), "enabled_modules": enabled}
        company.settings = new_settings
        company.save(update_fields=["settings", "updated_at"])
        return Response({"settings": company.settings, "enabled_modules": enabled, "available_modules": Company.AVAILABLE_MODULES})


class HealthView(APIView):
    authentication_classes = []
    permission_classes = []
    renderer_classes = [JSONRenderer]
    http_method_names = ["get", "head", "options"]

    @extend_schema(responses=HealthSerializer)
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:
            return Response({"status": "database_unavailable"}, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={"Cache-Control": "no-store", "X-Health-Status": "database_unavailable"})
        return Response({"status": "ok"}, headers={"Cache-Control": "no-store", "X-Health-Status": "ok"})

    def head(self, request):
        response = self.get(request)
        return HttpResponse(status=response.status_code, content_type="application/json", headers={"Cache-Control": "no-store", "X-Health-Status": response["X-Health-Status"]})


@extend_schema(exclude=True)
class MonitoringHealthView(HealthView):
    """Public aliases; the API health endpoint remains in the OpenAPI schema."""
