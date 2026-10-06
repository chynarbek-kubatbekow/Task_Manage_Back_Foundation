from django.contrib import admin, messages
from django.db.models import Q
from django.db import transaction
from django.core.exceptions import PermissionDenied

from .models import AuditLog, Company, Membership
from .admin_forms import CompanyRelationForm


NAMESPACES = {"company": "settings", "role": "roles", "membership": "employees", "client": "clients",
    "service": "services", "resource": "resources", "statusdefinition": "settings", "workschedule": "schedule",
    "timeoff": "schedule", "appointment": "appointments", "task": "tasks", "comment": "comments", "auditlog": "audit"}


def memberships(request):
    if not hasattr(request, "_admin_memberships"):
        request._admin_memberships = list(Membership.objects.filter(user=request.user, is_active=True, company__is_active=True).select_related("company").prefetch_related("roles"))
    return request._admin_memberships


def allows(member, permission):
    granted = {permission for role in member.roles.all() for permission in role.permissions}
    module = permission.split(".", 1)[0]
    return (module not in Company.AVAILABLE_MODULES or member.company.module_enabled(module)) and ("*" in granted or permission in granted)


class ScopedAdmin(admin.ModelAdmin):
    form = CompanyRelationForm
    list_select_related = True
    save_on_top = True

    def get_list_filter(self, request):
        filters = super().get_list_filter(request)
        if request.user.is_superuser:
            return filters
        return [(name, admin.RelatedOnlyFieldListFilter) if isinstance(name, str) and self.model._meta.get_field(name).is_relation else name for name in filters]

    @property
    def namespace(self):
        return NAMESPACES[self.model._meta.model_name]

    def permission_names(self, action):
        if self.namespace == "comments":
            return [f"tasks.{action}", f"appointments.{action}"]
        if self.model._meta.model_name == "statusdefinition" and action == "view":
            return ["settings.view", "appointments.view", "tasks.view"]
        return [f"{self.namespace}.{action}"]

    def permitted_members(self, request, action="view"):
        return [member for member in memberships(request) if any(allows(member, permission) for permission in self.permission_names(action))]

    def allowed(self, request, action, obj=None):
        if request.user.is_superuser:
            return True
        if not request.user.is_staff or not request.user.is_active:
            return False
        company_id = obj.pk if isinstance(obj, Company) else getattr(obj, "company_id", None)
        return any(company_id is None or member.company_id == company_id for member in self.permitted_members(request, action))

    def has_module_permission(self, request):
        return self.allowed(request, "view")

    def has_view_permission(self, request, obj=None):
        return self.allowed(request, "view", obj)

    def has_add_permission(self, request):
        return self.allowed(request, "manage")

    def has_change_permission(self, request, obj=None):
        return self.allowed(request, "manage", obj)

    def has_delete_permission(self, request, obj=None):
        if self.model is Company or any(field.name == "is_active" for field in self.model._meta.fields):
            return False
        return self.allowed(request, "manage", obj)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        members = self.permitted_members(request)
        if self.model is Company:
            return queryset.filter(pk__in=[member.company_id for member in members])
        scope = Q(pk__in=[])
        for member in members:
            full = allows(member, f"{self.namespace}.view_all") or allows(member, f"{self.namespace}.manage_all")
            company = Q(company_id=member.company_id)
            name = self.model._meta.model_name
            if name in {"appointment", "workschedule", "timeoff"} and not full:
                company &= Q(employee=member)
            elif name == "membership" and not full:
                company &= Q(pk=member.pk)
            elif name == "task" and not full:
                company &= Q(assignees=member)
            elif name == "client" and not full and not allows(member, "clients.manage"):
                company &= Q(appointments__employee=member)
            elif name == "comment":
                company &= (Q(task__isnull=False) if allows(member,"tasks.view_all") else Q(task__assignees=member)) | (Q(appointment__isnull=False) if allows(member,"appointments.view_all") else Q(appointment__employee=member))
            scope |= company
        return queryset.filter(scope).distinct()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if not request.user.is_superuser:
            companies = [member.company_id for member in self.permitted_members(request, "manage")]
            if related is Company:
                kwargs["queryset"] = Company.objects.filter(pk__in=companies)
            elif any(field.name == "company" for field in related._meta.fields):
                kwargs["queryset"] = related.objects.filter(company_id__in=companies)
            if db_field.name == "employee" and self.namespace in {"appointments","schedule"}:
                permitted = self.permitted_members(request,"manage")
                scope = Q(pk__in=[])
                for member in permitted:
                    scope |= Q(company_id=member.company_id) if allows(member,f"{self.namespace}.view_all") or allows(member,f"{self.namespace}.manage_all") else Q(pk=member.pk)
                kwargs["queryset"] = Membership.objects.filter(scope)
            if related._meta.label_lower == "auth.user":
                kwargs["queryset"] = related.objects.filter(memberships__company_id__in=companies).distinct()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if not request.user.is_superuser and any(field.name == "company" for field in related._meta.fields):
            kwargs["queryset"] = related.objects.filter(company_id__in=[member.company_id for member in self.permitted_members(request,"manage")])
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not self.allowed(request, "manage", obj):
            raise PermissionDenied
        if change:
            old = self.model.objects.get(pk=obj.pk)
            if hasattr(old,"company_id") and old.company_id != obj.company_id:
                raise PermissionDenied("Перенос данных между компаниями запрещён.")
        if hasattr(obj, "created_by_id") and not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        self.audit(request, obj, "update" if change else "create")

    def audit(self, request, obj, action):
        AuditLog.objects.create(company=obj if isinstance(obj, Company) else obj.company, actor=request.user,
            action=action, model_name=obj._meta.label_lower, object_id=str(obj.pk), object_repr=str(obj)[:255], changes={"source":"admin"})

    @transaction.atomic
    def delete_model(self, request, obj):
        self.audit(request, obj, "delete")
        super().delete_model(request, obj)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        for obj in queryset:
            self.audit(request, obj, "delete")
        super().delete_queryset(request, queryset)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if any(field.name == "is_active" for field in self.model._meta.fields) and self.model is not Company and self.has_change_permission(request):
            for name in ("archive_records", "restore_records"):
                action = getattr(type(self),name)
                actions[name] = (action, name, action.short_description)
        return actions

    @admin.action(description="Отправить выбранные объекты в архив", permissions=["change"])
    @transaction.atomic
    def archive_records(self, request, queryset):
        for obj in queryset:
            obj.is_active=False; obj.save(update_fields=["is_active","updated_at"]); self.audit(request,obj,"archive")
        self.message_user(request,"Объекты отправлены в архив. История сохранена.", messages.SUCCESS)

    @admin.action(description="Восстановить выбранные объекты из архива", permissions=["change"])
    @transaction.atomic
    def restore_records(self, request, queryset):
        for obj in queryset:
            obj.is_active=True; obj.save(update_fields=["is_active","updated_at"]); self.audit(request,obj,"restore")
        self.message_user(request,"Объекты восстановлены.", messages.SUCCESS)
