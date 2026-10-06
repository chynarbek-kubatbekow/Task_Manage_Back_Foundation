from datetime import timedelta
from uuid import UUID

from django.db import connection, IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class CompanyNumbersMigrationTests(TransactionTestCase):
    migrate_from = [("app", "0003_alter_service_duration_minutes")]
    migrate_to = [("app", "0004_alter_client_options_client_diagnosis_and_more")]

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        self.latest = executor.loader.graph.leaf_nodes()
        # Restore the schema even if data setup or a migration assertion fails.
        self.addCleanup(self.restore_latest_schema)
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        Company = apps.get_model("app", "Company")
        Client = apps.get_model("app", "Client")
        Membership = apps.get_model("app", "Membership")
        User = apps.get_model("auth", "User")
        Status = apps.get_model("app", "StatusDefinition")
        Appointment = apps.get_model("app", "Appointment")
        self.company = Company.objects.create(name="Existing clinic", slug="migration-existing")
        self.other = Company.objects.create(name="Second clinic", slug="migration-second")
        self.patient_ids = []
        self.member_ids = []
        same_time = timezone.now() - timedelta(days=30)
        # Tied timestamps must be ordered by ID, rather than names or insertion order.
        for index in (3, 1, 2):
            patient = Client.objects.create(id=UUID(int=index), company=self.company,
                first_name=f"Patient {index}", last_name=f"Name {4-index}", notes="Keep this note")
            Client.objects.filter(pk=patient.pk).update(created_at=same_time)
            self.patient_ids.append(patient.pk)
            user = User.objects.create(username=f"migration-worker-{index}")
            member = Membership.objects.create(id=UUID(int=index + 10), company=self.company, user=user)
            Membership.objects.filter(pk=member.pk).update(created_at=same_time)
            self.member_ids.append(member.pk)
        self.other_patient = Client.objects.create(company=self.other, first_name="Other patient")
        other_user = User.objects.create(username="migration-other-worker")
        self.other_member = Membership.objects.create(company=self.other, user=other_user)
        status = Status.objects.create(company=self.company, entity_type="appointment", code="planned", name="Planned")
        now = timezone.now()
        self.booking = Appointment.objects.create(company=self.company, client_id=self.patient_ids[0],
            employee_id=self.member_ids[0], status=status, title="Existing appointment",
            starts_at=now, ends_at=now + timedelta(hours=1))
        self.original_updated = dict(Client.objects.values_list("pk", "updated_at"))
        if connection.vendor == "postgresql":
            self.install_deferred_trigger()
            self.addCleanup(self.remove_deferred_trigger)
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def restore_latest_schema(self):
        MigrationExecutor(connection).migrate(self.latest)

    def install_deferred_trigger(self):
        with connection.cursor() as cursor:
            cursor.execute("""CREATE OR REPLACE FUNCTION number_migration_test_trigger() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$""")
            cursor.execute("""CREATE CONSTRAINT TRIGGER number_migration_pending_check
                AFTER UPDATE ON app_client DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
                EXECUTE FUNCTION number_migration_test_trigger()""")

    def remove_deferred_trigger(self):
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER IF EXISTS number_migration_pending_check ON app_client")
            cursor.execute("DROP FUNCTION IF EXISTS number_migration_test_trigger()")

    def test_existing_data_and_foreign_keys_survive_numbering(self):
        Client = self.apps.get_model("app", "Client")
        Membership = self.apps.get_model("app", "Membership")
        Appointment = self.apps.get_model("app", "Appointment")
        for number, pk in enumerate(sorted(self.patient_ids), 1):
            patient = Client.objects.get(pk=pk)
            self.assertEqual(patient.patient_number, number)
            self.assertEqual(patient.notes, "Keep this note")
            self.assertEqual(patient.updated_at, self.original_updated[pk])
        for number, pk in enumerate(sorted(self.member_ids), 1):
            self.assertEqual(Membership.objects.get(pk=pk).employee_number, number)
        self.assertEqual(Client.objects.get(pk=self.other_patient.pk).patient_number, 1)
        self.assertEqual(Membership.objects.get(pk=self.other_member.pk).employee_number, 1)
        booking = Appointment.objects.get(pk=self.booking.pk)
        self.assertEqual(booking.client_id, self.patient_ids[0])
        self.assertEqual(booking.employee_id, self.member_ids[0])
        for Model, field, pk in ((Client, "patient_number", self.patient_ids[0]),
                                 (Membership, "employee_number", self.member_ids[0])):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Model.objects.filter(pk=pk).update(**{field: 1})

    def test_repeated_migrate_does_not_renumber_or_duplicate(self):
        Client = self.apps.get_model("app", "Client")
        before = list(Client.objects.order_by("pk").values_list("pk", "patient_number"))
        MigrationExecutor(connection).migrate(self.migrate_to)
        self.assertEqual(list(Client.objects.order_by("pk").values_list("pk", "patient_number")), before)
