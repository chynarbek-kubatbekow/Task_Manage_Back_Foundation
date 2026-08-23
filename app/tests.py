from datetime import datetime, time, timedelta
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Appointment, Client, Company, Membership, Role, StatusDefinition, WorkSchedule


class FoundationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Clinic", slug="clinic")
        self.other = Company.objects.create(name="Other", slug="other")
        self.user = get_user_model().objects.create_user("manager", password="safe-password")
        self.role = Role.objects.create(company=self.company, name="Manager", code="manager", permissions=["clients.view", "clients.view_all", "clients.manage", "appointments.view", "appointments.view_all", "appointments.manage"])
        self.member = Membership.objects.create(company=self.company, user=self.user)
        self.member.roles.add(self.role)
        self.status = StatusDefinition.objects.create(company=self.company, entity_type="appointment", name="New", code="new")
        self.client = Client.objects.create(company=self.company, first_name="Ivan")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_tenant_isolation(self):
        Client.objects.create(company=self.other, first_name="Hidden")
        response = self.api.get("/api/v1/clients/", HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["first_name"], "Ivan")

    def test_role_denies_ungranted_resource(self):
        response = self.api.get("/api/v1/tasks/", HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(response.status_code, 403)

    def test_appointment_overlap_rejected(self):
        start = timezone.now() + timedelta(days=1)
        Appointment.objects.create(company=self.company, client=self.client, employee=self.member, status=self.status, title="First", starts_at=start, ends_at=start + timedelta(hours=1))
        second = Appointment(company=self.company, client=self.client, employee=self.member, status=self.status, title="Second", starts_at=start + timedelta(minutes=30), ends_at=start + timedelta(hours=2))
        with self.assertRaises(ValidationError):
            second.clean()

    def test_cross_company_header_denied(self):
        response = self.api.get("/api/v1/clients/", HTTP_X_COMPANY_ID=str(self.other.id))
        self.assertEqual(response.status_code, 403)

    def test_me_returns_frontend_capabilities(self):
        response = self.api.get("/api/v1/me/", HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["capabilities"]["clients.view"])
        self.assertEqual(response.data["active_company"]["slug"], "clinic")

    def test_worker_sees_only_own_appointments(self):
        self.role.permissions = ["appointments.view", "appointments.manage"]
        self.role.save(update_fields=["permissions"])
        other_user = get_user_model().objects.create_user("other-worker", password="safe-password")
        other_member = Membership.objects.create(company=self.company, user=other_user)
        start = timezone.now() + timedelta(days=2)
        Appointment.objects.create(company=self.company, client=self.client, employee=self.member, status=self.status, title="Mine", starts_at=start, ends_at=start + timedelta(hours=1))
        Appointment.objects.create(company=self.company, client=self.client, employee=other_member, status=self.status, title="Hidden", starts_at=start, ends_at=start + timedelta(hours=1))
        response = self.api.get("/api/v1/appointments/", HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Mine")

    def test_delete_archives_client_and_restore_returns_it(self):
        response = self.api.delete(f"/api/v1/clients/{self.client.id}/", HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Client.objects.get(pk=self.client.id).is_active)
        response = self.api.post(f"/api/v1/clients/{self.client.id}/restore/", HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Client.objects.get(pk=self.client.id).is_active)

    def test_disabled_module_is_blocked(self):
        self.company.settings = {"enabled_modules": ["appointments", "tasks"]}
        self.company.save(update_fields=["settings"])
        response = self.api.get("/api/v1/clients/", HTTP_X_COMPANY_ID=str(self.company.id))
        self.assertEqual(response.status_code, 403)

    def test_available_slots_are_returned_per_employee(self):
        selected = timezone.localdate() + timedelta(days=1)
        WorkSchedule.objects.filter(
            company=self.company, employee=self.member, weekday=selected.weekday(),
        ).update(start_time=time(9), end_time=time(11))
        response = self.api.get(
            "/api/v1/appointments/available-slots/",
            {"employee": self.member.id, "date": selected.isoformat(), "duration": 60},
            HTTP_X_COMPANY_ID=str(self.company.id),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["slots"]), 2)
        self.assertEqual(response.data["duration_minutes"], 60)
        self.assertEqual(response.data["step_minutes"], 60)

    def test_environment_bootstrap_is_idempotent_and_keeps_password(self):
        variables = {
            "BOOTSTRAP_COMPANY_NAME": "Environment Company",
            "BOOTSTRAP_COMPANY_SLUG": "environment-company",
            "BOOTSTRAP_ADMIN_USERNAME": "environment-owner",
            "BOOTSTRAP_ADMIN_EMAIL": "owner@example.com",
            "BOOTSTRAP_ADMIN_PASSWORD": "InitialStrongPassword123!",
        }
        with patch.dict(os.environ, variables):
            call_command("bootstrap_from_env")
        user = get_user_model().objects.get(username="environment-owner")
        self.assertTrue(user.check_password("InitialStrongPassword123!"))
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        with patch.dict(os.environ, {**variables, "BOOTSTRAP_ADMIN_PASSWORD": "DoNotReplacePassword123!"}):
            call_command("bootstrap_from_env")
        user.refresh_from_db()
        self.assertTrue(user.check_password("InitialStrongPassword123!"))
        self.assertEqual(Membership.objects.filter(user=user, company__slug="environment-company").count(), 1)

    def test_health_supports_head_without_authentication(self):
        anonymous = APIClient()
        response = anonymous.head("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_backend_root_is_health_endpoint(self):
        anonymous = APIClient()
        response = anonymous.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "ok"})
        response = anonymous.head("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")

    def test_cors_preflight_allows_company_header(self):
        response = self.api.options(
            "/api/auth/token/",
            HTTP_ORIGIN="http://localhost:5173",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-company-id",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("x-company-id", response["Access-Control-Allow-Headers"])

    def test_new_membership_receives_default_weekday_schedule(self):
        user = get_user_model().objects.create_user("scheduled-worker", password="safe-password")
        membership = Membership.objects.create(company=self.company, user=user)
        self.assertEqual(WorkSchedule.objects.filter(employee=membership).count(), 5)

    def test_worker_can_create_ninety_minute_appointment_at_exact_minute_and_delete_it(self):
        selected = timezone.localdate() + timedelta(days=1)
        while selected.weekday() >= 5:
            selected += timedelta(days=1)
        starts_at = timezone.make_aware(datetime.combine(selected, time(10, 7)))
        response = self.api.post(
            "/api/v1/appointments/",
            {
                "title": "Exact time appointment",
                "client": str(self.client.id),
                "employee": str(self.member.id),
                "status": str(self.status.id),
                "resources": [],
                "starts_at": starts_at.isoformat(),
                "ends_at": (starts_at + timedelta(minutes=90)).isoformat(),
                "notes": "",
                "extra_data": {},
            },
            format="json",
            HTTP_X_COMPANY_ID=str(self.company.id),
        )
        self.assertEqual(response.status_code, 201, response.data)
        appointment_id = response.data["id"]
        response = self.api.delete(
            f"/api/v1/appointments/{appointment_id}/",
            HTTP_X_COMPANY_ID=str(self.company.id),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Appointment.objects.filter(id=appointment_id).exists())
