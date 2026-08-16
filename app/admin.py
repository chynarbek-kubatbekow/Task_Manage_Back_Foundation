from django.contrib import admin

from .models import (Appointment, AuditLog, Client, Comment, Company, Membership,
                     Resource, Role, Service, StatusDefinition, Task, TimeOff, WorkSchedule)


admin.site.site_header = "Фундамент — управление системой"
admin.site.site_title = "Фундамент"
admin.site.index_title = "Компании, пользователи и рабочие данные"


class CompanyFilterAdmin(admin.ModelAdmin):
    list_filter = ["company"]
    autocomplete_fields = []


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "timezone", "is_active", "created_at"]
    list_filter = ["is_active", "timezone"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Role)
class RoleAdmin(CompanyFilterAdmin):
    list_display = ["name", "code", "company", "is_system"]
    search_fields = ["name", "code", "company__name"]
    prepopulated_fields = {"code": ("name",)}


@admin.register(Membership)
class MembershipAdmin(CompanyFilterAdmin):
    list_display = ["user", "company", "job_title", "is_active"]
    list_filter = ["company", "is_active", "roles"]
    search_fields = ["user__username", "user__first_name", "user__last_name", "phone", "job_title"]
    filter_horizontal = ["roles"]


@admin.register(Client)
class ClientAdmin(CompanyFilterAdmin):
    list_display = ["__str__", "company", "phone", "email", "is_active", "updated_at"]
    list_filter = ["company", "is_active"]
    search_fields = ["first_name", "last_name", "phone", "email", "notes"]


@admin.register(Service)
class ServiceAdmin(CompanyFilterAdmin):
    list_display = ["name", "category", "duration_minutes", "price", "is_active", "company"]
    list_filter = ["company", "category", "is_active"]
    search_fields = ["name", "description"]


@admin.register(Resource)
class ResourceAdmin(CompanyFilterAdmin):
    list_display = ["name", "resource_type", "is_active", "company"]
    list_filter = ["company", "resource_type", "is_active"]
    search_fields = ["name", "resource_type"]


@admin.register(StatusDefinition)
class StatusAdmin(CompanyFilterAdmin):
    list_display = ["name", "entity_type", "company", "color", "is_default", "is_closed"]
    list_filter = ["company", "entity_type", "is_default", "is_closed"]
    search_fields = ["name", "code"]
    prepopulated_fields = {"code": ("name",)}


@admin.register(WorkSchedule)
class WorkScheduleAdmin(CompanyFilterAdmin):
    list_display = ["employee", "company", "weekday", "start_time", "end_time", "is_active"]
    list_filter = ["company", "weekday", "is_active"]
    search_fields = ["employee__user__username", "employee__user__first_name", "employee__user__last_name"]


@admin.register(TimeOff)
class TimeOffAdmin(CompanyFilterAdmin):
    list_display = ["employee", "starts_at", "ends_at", "is_approved", "company"]
    list_filter = ["company", "is_approved"]


@admin.register(Appointment)
class AppointmentAdmin(CompanyFilterAdmin):
    list_display = ["title", "client", "employee", "starts_at", "status", "company"]
    list_filter = ["company", "status", "employee", "cancelled_at"]
    search_fields = ["title", "client__first_name", "client__last_name", "client__phone", "notes"]
    date_hierarchy = "starts_at"


@admin.register(Task)
class TaskAdmin(CompanyFilterAdmin):
    list_display = ["title", "status", "priority", "due_at", "company"]
    list_filter = ["company", "status", "priority", "assignees"]
    search_fields = ["title", "description"]
    filter_horizontal = ["assignees"]


@admin.register(Comment)
class CommentAdmin(CompanyFilterAdmin):
    list_display = ["author", "task", "appointment", "company", "created_at"]
    search_fields = ["text", "author__username"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["created_at", "actor", "action", "model_name", "object_repr", "company"]
    list_filter = ["company", "action", "model_name"]
    search_fields = ["object_repr", "object_id", "actor__username"]
    readonly_fields = [field.name for field in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
