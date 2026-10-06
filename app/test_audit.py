from datetime import datetime, time, timedelta
from io import StringIO
from unittest.mock import patch
import os

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import OperationalError
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Appointment, AuditLog, Client, Company, Membership, Role, StatusDefinition, Task, WorkSchedule
from .admin_forms import CompanyForm


class BackendAuditTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name="Audit Clinic",slug="audit")
        self.other=Company.objects.create(name="Other Clinic",slug="other-audit")
        self.user=get_user_model().objects.create_user("audit-user",password="safe-password",is_staff=True)
        self.member=Membership.objects.create(user=self.user,company=self.company)
        self.role=Role.objects.create(company=self.company,name="Owner",code="owner",permissions=["*"])
        self.member.roles.add(self.role)
        self.status=StatusDefinition.objects.create(company=self.company,entity_type="appointment",code="planned",name="Planned",is_default=True)
        self.task_status=StatusDefinition.objects.create(company=self.company,entity_type="task",code="new",name="New",is_default=True)
        self.patient=Client.objects.create(company=self.company,first_name="Test",diagnosis="Private diagnosis",doctor_notes="Private notes")
        self.api=APIClient(); self.api.force_authenticate(self.user)
        self.headers={"HTTP_X_COMPANY_ID":str(self.company.pk)}

    def future_start(self):
        date=timezone.localdate()+timedelta(days=7)
        while date.weekday()>=5: date+=timedelta(days=1)
        return timezone.make_aware(datetime.combine(date,time(10)))

    def booking(self, start=None, **extra):
        start=start or self.future_start()
        return Appointment.objects.create(company=self.company,client=self.patient,employee=self.member,status=self.status,
            title="Test booking",starts_at=start,ends_at=start+timedelta(hours=1),**extra)

    def test_head_all_monitor_urls_without_auth(self):
        anonymous=APIClient()
        for path in ("/","/health/","/health","/api/v1/health/"):
            response=anonymous.head(path)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.content,b"")
            self.assertEqual(response["X-Health-Status"],"ok")
            self.assertEqual(response["Cache-Control"],"no-store")

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_internal_health_check_executes_instead_of_redirecting(self):
        anonymous=APIClient()
        self.assertEqual(anonymous.head("/api/v1/health/").status_code,200)
        self.assertEqual(anonymous.get("/admin/login/").status_code,301)
        with patch("app.views.connection.cursor",side_effect=OperationalError("Unavailable")):
            for method in (anonymous.get,anonymous.head):
                response=method("/api/v1/health/")
                self.assertEqual(response.status_code,503)
                self.assertEqual(response["X-Health-Status"],"database_unavailable")

    def test_bootstrap_without_password_does_not_block_start(self):
        with patch.dict(os.environ,{"BOOTSTRAP_ADMIN_PASSWORD":""}):
            output=StringIO(); call_command("bootstrap_from_env",stdout=output)
        self.assertIn("Bootstrap skipped",output.getvalue())

    def test_restart_preserves_custom_roles_statuses_and_split_schedules(self):
        options={"company":"Bootstrap","slug":"bootstrap-audit","username":"bootstrap-audit","email":"qa@example.com","password":"InitialStrongPassword123!"}
        call_command("bootstrap_company",**options,stdout=StringIO())
        company=Company.objects.get(slug=options["slug"])
        role=Role.objects.get(company=company,code="manager"); role.permissions=["clients.view"]; role.save()
        status=StatusDefinition.objects.get(company=company,entity_type="appointment",code="planned"); status.name="Custom label"; status.save()
        member=Membership.objects.get(company=company)
        WorkSchedule.objects.create(company=company,employee=member,weekday=0,start_time=time(19),end_time=time(20))
        call_command("bootstrap_company",**options,stdout=StringIO())
        role.refresh_from_db(); status.refresh_from_db()
        self.assertEqual(role.permissions,["clients.view"])
        self.assertEqual(status.name,"Custom label")
        self.assertEqual(WorkSchedule.objects.filter(employee=member,weekday=0).count(),2)

    def test_role_admin_preserves_wildcard_and_custom_permissions(self):
        self.role.permissions=["*","tasks.manage_all"]; self.role.save()
        role_admin=admin.site._registry[Role]
        request=RequestFactory().get("/admin/"); request.user=self.user
        form_class=role_admin.get_form(request,self.role)
        form=form_class(data={"company":str(self.company.pk),"name":"Owner","code":"owner","permission_flags":["*","tasks.manage_all"],"is_system":False},instance=self.role)
        self.assertTrue(form.is_valid(),form.errors)
        form.save(); self.role.refresh_from_db()
        self.assertEqual(self.role.permissions,["*","tasks.manage_all"])

    def test_admin_lists_and_post_choices_do_not_leak_other_tenant(self):
        foreign_patient=Client.objects.create(company=self.other,first_name="Hidden patient")
        foreign_role=Role.objects.create(company=self.other,name="Foreign",code="foreign")
        foreign_user=get_user_model().objects.create_user("foreign-doctor")
        Membership.objects.create(company=self.other,user=foreign_user)
        request=RequestFactory().get("/admin/"); request.user=self.user
        client_admin=admin.site._registry[Client]
        self.assertNotIn(foreign_patient,client_admin.get_queryset(request))
        membership_admin=admin.site._registry[Membership]
        form_class=membership_admin.get_form(request,self.member)
        form=form_class(data={"user":self.user.pk,"company":str(self.company.pk),"roles":[str(foreign_role.pk)],"color":"#64748b","is_active":True,"extra_data":"{}"},instance=self.member)
        self.assertFalse(form.is_valid())
        self.assertIn("roles",form.errors)
        self.assertFalse(client_admin.has_change_permission(request,foreign_patient))

    def test_staff_cannot_access_global_user_admin(self):
        request=RequestFactory().get("/admin/"); request.user=self.user
        self.assertFalse(admin.site._registry[get_user_model()].has_change_permission(request,self.user))

    def test_appointment_brief_includes_names_without_clinical_fields(self):
        booking=self.booking()
        response=self.api.get(f"/api/v1/appointments/{booking.pk}/",**self.headers)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data["client_detail"]["first_name"],"Test")
        self.assertNotIn("diagnosis",response.data["client_detail"])
        self.assertNotIn("doctor_notes",response.data["client_detail"])
        self.assertEqual(response.data["employee_detail"]["user_detail"]["username"],"audit-user")

    def test_invalid_filters_and_uuids_are_handled(self):
        response=self.api.get("/api/v1/clients/",HTTP_X_COMPANY_ID="not-a-uuid")
        self.assertEqual(response.status_code,404)
        for query in ({"start":"wrong"},{"start":"2026-10-08T00:00:00Z","end":"2026-10-06T00:00:00Z"}):
            self.assertEqual(self.api.get("/api/v1/appointments/",query,**self.headers).status_code,400)
        self.assertEqual(self.api.get("/api/v1/appointments/available-slots/",{"employee":"wrong","date":"2026-11-02"},**self.headers).status_code,400)

    def test_frontend_settings_payload_and_invalid_types(self):
        self.company.settings={"enabled_modules":Company.AVAILABLE_MODULES,"brand":"Clinic"}; self.company.save()
        response=self.api.patch("/api/v1/company-settings/",{"settings":self.company.settings,"enabled_modules":["clients","appointments"]},format="json",**self.headers)
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data["enabled_modules"],["clients","appointments"])
        self.assertEqual(response.data["settings"]["brand"],"Clinic")
        for data in ({"settings":[]},{"enabled_modules":None},{"enabled_modules":["unknown"]}):
            self.assertEqual(self.api.patch("/api/v1/company-settings/",data,format="json",**self.headers).status_code,400)

    def test_task_closed_and_reopened_status_updates_completion(self):
        done=StatusDefinition.objects.create(company=self.company,entity_type="task",code="done",name="Done",is_closed=True)
        task=Task.objects.create(company=self.company,title="Task",status=self.task_status)
        task.assignees.add(self.member)
        response=self.api.patch(f"/api/v1/tasks/{task.pk}/",{"status":str(done.pk)},format="json",**self.headers)
        self.assertEqual(response.status_code,200); self.assertIsNotNone(response.data["completed_at"])
        response=self.api.patch(f"/api/v1/tasks/{task.pk}/",{"status":str(self.task_status.pk)},format="json",**self.headers)
        self.assertEqual(response.status_code,200); self.assertIsNone(response.data["completed_at"])

    def test_only_one_default_status_per_entity_and_membership_permission_on_sqlite(self):
        StatusDefinition.objects.create(company=self.company,entity_type="appointment",name="New default",code="replacement",is_default=True)
        self.status.refresh_from_db()
        self.assertFalse(self.status.is_default)
        self.assertTrue(self.member.has_permission("clients.manage"))

    def test_resource_conflict_is_checked_when_resources_omitted_from_patch(self):
        from .models import Resource
        resource=Resource.objects.create(company=self.company,name="Room")
        first=self.booking(); first.resources.add(resource)
        user=get_user_model().objects.create_user("second-doctor"); member=Membership.objects.create(company=self.company,user=user)
        second=Appointment.objects.create(company=self.company,client=self.patient,employee=member,status=self.status,title="Room busy",starts_at=first.starts_at+timedelta(hours=2),ends_at=first.ends_at+timedelta(hours=2)); second.resources.add(resource)
        response=self.api.patch(f"/api/v1/appointments/{first.pk}/",{"starts_at":second.starts_at.isoformat(),"ends_at":second.ends_at.isoformat()},format="json",**self.headers)
        self.assertEqual(response.status_code,400,response.data)

    def test_comment_requires_one_target_and_respects_worker_scope(self):
        self.role.permissions=["tasks.view","tasks.manage","appointments.view","appointments.manage"]; self.role.save()
        response=self.api.post("/api/v1/comments/",{"text":"No target"},format="json",**self.headers)
        self.assertEqual(response.status_code,400)
        foreign=Task.objects.create(company=self.company,title="Someone else's task",status=self.task_status)
        response=self.api.post("/api/v1/comments/",{"text":"Hidden","task":str(foreign.pk)},format="json",**self.headers)
        self.assertEqual(response.status_code,400)

    def test_company_form_validates_timezone_and_saves_module_checkboxes(self):
        form=CompanyForm(data={"name":self.company.name,"slug":self.company.slug,"timezone":"Asia/Almaty","is_active":True,"settings":"{}","enabled_modules":["clients","tasks"]},instance=self.company)
        self.assertTrue(form.is_valid(),form.errors); form.save()
        self.company.refresh_from_db(); self.assertEqual(self.company.settings["enabled_modules"],["clients","tasks"])
        form=CompanyForm(data={"name":self.company.name,"slug":self.company.slug,"timezone":"Invalid/Timezone","settings":"{}"},instance=self.company)
        self.assertFalse(form.is_valid()); self.assertIn("timezone",form.errors)

    def test_admin_pages_render_and_filters_do_not_reveal_other_company(self):
        self.client.force_login(self.user)
        self.booking()
        for model in (Company, Role, Membership, Client, StatusDefinition, WorkSchedule, Appointment, Task, AuditLog):
            label = model._meta.model_name
            response = self.client.get(f"/admin/app/{label}/")
            self.assertEqual(response.status_code, 200, label)
            self.assertNotContains(response, "Other Clinic")
            if model is not AuditLog:
                response = self.client.get(f"/admin/app/{label}/add/")
                self.assertEqual(response.status_code, 403 if model is Company else 200, label)
        response = self.client.get(f"/admin/app/client/{self.patient.pk}/change/")
        self.assertEqual(response.status_code, 200)

    def test_protected_status_delete_returns_conflict_without_false_audit(self):
        self.booking()
        before = AuditLog.objects.count()
        response = self.api.delete(f"/api/v1/statuses/{self.status.pk}/", **self.headers)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(StatusDefinition.objects.filter(pk=self.status.pk).exists())
        self.assertEqual(AuditLog.objects.count(), before)

    def test_hard_delete_audit_keeps_deleted_object_id(self):
        patient = Client.objects.create(company=self.company, first_name="Unused")
        object_id = str(patient.pk)
        response = self.api.delete(f"/api/v1/clients/{object_id}/hard-delete/", **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(AuditLog.objects.get(action="hard_delete").object_id, object_id)

    def test_system_role_cannot_be_deleted_through_api_or_admin(self):
        self.role.is_system = True; self.role.save()
        response = self.api.delete(f"/api/v1/roles/{self.role.pk}/", **self.headers)
        self.assertEqual(response.status_code, 400, response.data)
        request = RequestFactory().get("/admin/"); request.user = self.user
        role_admin = admin.site._registry[Role]
        self.assertFalse(role_admin.has_delete_permission(request, self.role))
        self.assertNotIn("delete_selected", role_admin.get_actions(request))

    def test_admin_booking_form_rejects_time_outside_work_schedule(self):
        request = RequestFactory().get("/admin/"); request.user = self.user
        form_class = admin.site._registry[Appointment].get_form(request)
        start = self.future_start().replace(hour=22)
        form = form_class(data={"company":str(self.company.pk), "client":str(self.patient.pk),
            "employee":str(self.member.pk), "status":str(self.status.pk), "title":"After hours",
            "starts_at_0":start.date().isoformat(), "starts_at_1":start.strftime("%H:%M:%S"),
            "ends_at_0":start.date().isoformat(), "ends_at_1":"23:00:00", "extra_data":"{}"})
        self.assertFalse(form.is_valid())
        self.assertIn("starts_at", form.errors)

    def test_employee_creation_rejects_role_from_another_company(self):
        foreign_role = Role.objects.create(company=self.other, name="Foreign", code="foreign")
        response = self.api.post("/api/v1/users/", {"username":"new-worker", "password":"StrongTestPassword!837",
            "role_ids":[str(foreign_role.pk)]}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("role_ids", response.data)
        self.assertFalse(get_user_model().objects.filter(username="new-worker").exists())

    def test_duplicate_company_keys_return_validation_errors(self):
        for path, payload, field in (
            ("roles", {"name":"Duplicate", "code":self.role.code}, "code"),
            ("statuses", {"name":"Duplicate", "code":self.status.code, "entity_type":"appointment"}, "code"),
            ("employees", {"user":self.user.pk}, "user"),
        ):
            response = self.api.post(f"/api/v1/{path}/", payload, format="json", **self.headers)
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn(field, response.data)

    def test_bootstrap_does_not_replace_custom_default_status(self):
        options = {"company":"Bootstrap", "slug":"bootstrap-custom", "username":"bootstrap-custom",
            "email":"qa@example.com", "password":"InitialStrongPassword123!"}
        call_command("bootstrap_company", **options, stdout=StringIO())
        company = Company.objects.get(slug=options["slug"])
        StatusDefinition.objects.filter(company=company, entity_type="appointment", code="planned").delete()
        custom = StatusDefinition.objects.create(company=company, entity_type="appointment", code="custom", name="Custom", is_default=True)
        call_command("bootstrap_company", **options, stdout=StringIO())
        custom.refresh_from_db()
        self.assertTrue(custom.is_default)
        self.assertFalse(StatusDefinition.objects.get(company=company, entity_type="appointment", code="planned").is_default)
