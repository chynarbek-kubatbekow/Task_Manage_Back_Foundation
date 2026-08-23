from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db import models as django_models

from .models import (Appointment, AuditLog, Client, Comment, Company, Membership,
                     Resource, Role, Service, StatusDefinition, Task, TimeOff, WorkSchedule)


admin.site.site_header = "Фундамент — управление системой"
admin.site.site_title = "Фундамент"
admin.site.index_title = "Компании, пользователи и рабочие данные"


class MembershipInline(admin.StackedInline):
    model = Membership
    extra = 1
    fields = ["company", "roles", "job_title", "phone", "color", "is_active"]
    formfield_overrides = {
        django_models.ManyToManyField: {"widget": forms.CheckboxSelectMultiple},
    }
    verbose_name = "Доступ работника к компании"
    verbose_name_plural = "Доступ работника к компаниям"

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "company":
            field.label = "Клиника"
            field.queryset = field.queryset.order_by("name")
        return field


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    inlines = [MembershipInline]
    list_display = ["username", "email", "first_name", "last_name", "is_staff", "is_active"]
    search_fields = ["username", "first_name", "last_name", "email"]


class CompanyFilterAdmin(admin.ModelAdmin):
    list_filter = ["company"]
    autocomplete_fields = []

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        field = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "company":
            field.label = "Клиника"
            field.queryset = field.queryset.order_by("name")
        return field


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "timezone", "is_active", "created_at"]
    list_filter = ["is_active", "timezone"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Role)
class RoleAdmin(CompanyFilterAdmin):
    PERMISSION_CHOICES = [
        ("dashboard.view", "Главная: просмотр"),
        ("dashboard.view_all", "Главная: видеть всю компанию"),
        ("clients.view", "Пациенты: просмотр"),
        ("clients.manage", "Пациенты: добавление, изменение и архив"),
        ("clients.view_all", "Пациенты: видеть всю компанию"),
        ("appointments.view", "Записи: просмотр"),
        ("appointments.manage", "Записи: создание, перенос и удаление"),
        ("appointments.view_all", "Записи: видеть всех врачей"),
        ("schedule.view", "График: просмотр"),
        ("schedule.manage", "График: изменение"),
        ("schedule.view_all", "График: видеть всю команду"),
        ("tasks.view", "Задачи: просмотр"),
        ("tasks.manage", "Задачи: управление"),
        ("tasks.view_all", "Задачи: видеть всю команду"),
        ("employees.view", "Работники: просмотр"),
        ("employees.manage", "Работники: управление"),
        ("employees.view_all", "Работники: видеть всю команду"),
        ("services.view", "Услуги: просмотр"),
        ("services.manage", "Услуги: управление"),
        ("resources.view", "Кабинеты и ресурсы: просмотр"),
        ("resources.manage", "Кабинеты и ресурсы: управление"),
        ("settings.view", "Настройки: просмотр"),
        ("settings.manage", "Настройки: управление"),
        ("roles.view", "Роли: просмотр"),
        ("roles.manage", "Роли: управление"),
        ("audit.view", "История действий: просмотр"),
    ]

    class RoleForm(forms.ModelForm):
        permission_flags = forms.MultipleChoiceField(
            label="Возможности роли",
            choices=[],
            required=False,
            widget=forms.CheckboxSelectMultiple,
            help_text="Поставьте галочки напротив разрешённых действий.",
        )

        class Meta:
            model = Role
            exclude = ["permissions"]

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.fields["permission_flags"].choices = RoleAdmin.PERMISSION_CHOICES
            if self.instance and self.instance.pk:
                self.initial["permission_flags"] = self.instance.permissions

        def save(self, commit=True):
            instance = super().save(commit=False)
            instance.permissions = self.cleaned_data.get("permission_flags", [])
            if commit:
                instance.save()
            return instance

    form = RoleForm
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
