from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Appointment, Client, Company, Membership, Role, StatusDefinition


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
