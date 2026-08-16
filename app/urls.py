from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AppointmentViewSet, AuditLogViewSet, ClientViewSet, CommentViewSet,
                    CompanySettingsView, CompanyViewSet, DashboardView, HealthView, MembershipViewSet, MeView, ResourceViewSet,
                    RoleViewSet, ServiceViewSet, StatusViewSet, TaskViewSet, TimeOffViewSet,
                    UserViewSet, WorkScheduleViewSet)

router = DefaultRouter()
router.register("companies", CompanyViewSet, basename="company")
router.register("roles", RoleViewSet)
router.register("employees", MembershipViewSet)
router.register("users", UserViewSet, basename="user")
router.register("clients", ClientViewSet)
router.register("services", ServiceViewSet)
router.register("resources", ResourceViewSet)
router.register("statuses", StatusViewSet)
router.register("schedules", WorkScheduleViewSet)
router.register("time-off", TimeOffViewSet)
router.register("appointments", AppointmentViewSet)
router.register("tasks", TaskViewSet)
router.register("comments", CommentViewSet)
router.register("audit", AuditLogViewSet, basename="audit")

urlpatterns = [path("", include(router.urls)), path("me/", MeView.as_view()), path("company-settings/", CompanySettingsView.as_view()), path("dashboard/", DashboardView.as_view()), path("health/", HealthView.as_view())]
