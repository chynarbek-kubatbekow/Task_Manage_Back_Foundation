from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import Company, Membership


class CompanyContextMixin:
    """Resolves the active tenant and prevents cross-company data access."""

    def get_company(self):
        if hasattr(self, "_company"):
            return self._company
        company_id = self.request.headers.get("X-Company-ID") or self.request.query_params.get("company")
        if not company_id:
            raise ValidationError({"company": "Передайте X-Company-ID в заголовке."})
        try:
            company = Company.objects.get(pk=company_id, is_active=True)
        except (Company.DoesNotExist, ValueError, DjangoValidationError):
            raise NotFound("Компания не найдена.")
        if not self.request.user.is_superuser:
            membership = Membership.objects.filter(user=self.request.user, company=company, is_active=True).prefetch_related("roles").first()
            if not membership:
                raise PermissionDenied("Нет доступа к этой компании.")
            self.request.membership = membership
        self._company = company
        return company

    def get_granted_permissions(self):
        if self.request.user.is_superuser:
            return {"*"}
        self.get_company()
        granted = set()
        for role in self.request.membership.roles.all():
            granted.update(role.permissions)
        return granted

    def has_product_permission(self, permission):
        granted = self.get_granted_permissions()
        return "*" in granted or permission in granted


class RolePermission(BasePermission):
    """Maps safe/write requests to configurable role permission strings."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        company = view.get_company()
        module = getattr(view, "module_key", None)
        if module and not company.module_enabled(module):
            raise PermissionDenied(f"Модуль '{module}' отключён для компании.")
        if request.user.is_superuser:
            return True
        required = getattr(view, "permission_map", {}).get(view.action)
        namespace = getattr(view, "permission_namespace", module)
        if not required and view.action in {"restore", "hard_delete"} and namespace:
            required = f"{namespace}.delete" if view.action == "hard_delete" else f"{namespace}.manage"
        if not required:
            return True
        if isinstance(required, str):
            required = [required]
        granted = view.get_granted_permissions()
        return "*" in granted or bool(granted.intersection(required))
