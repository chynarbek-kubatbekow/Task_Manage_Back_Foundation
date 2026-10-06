from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Company, Appointment, Task, Membership, Resource
from .booking import validate_window


MODULE_LABELS = {
    "clients": "Пациенты", "employees": "Команда", "services": "Услуги",
    "resources": "Кабинеты и оборудование", "schedule": "Рабочие графики",
    "appointments": "Записи", "tasks": "Задачи", "comments": "Комментарии", "audit": "История",
}


class CompanyForm(forms.ModelForm):
    enabled_modules = forms.MultipleChoiceField(label="Разделы фронтенда", required=False,
        choices=[(key, MODULE_LABELS[key]) for key in Company.AVAILABLE_MODULES], widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Company
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial["enabled_modules"] = self.instance.settings.get("enabled_modules", Company.AVAILABLE_MODULES) if isinstance(self.instance.settings, dict) else Company.AVAILABLE_MODULES
        self.fields["settings"].help_text = "Брендинг и дополнительные параметры. Разделы включаются галочками выше."

    def clean_timezone(self):
        value = self.cleaned_data["timezone"]
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError("Укажите часовой пояс IANA, например Asia/Almaty.")
        return value

    def clean_settings(self):
        value = self.cleaned_data.get("settings") or {}
        if not isinstance(value, dict):
            raise ValidationError("Настройки должны быть JSON-объектом.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.settings = {**(self.cleaned_data.get("settings") or {}), "enabled_modules": self.cleaned_data["enabled_modules"]}
        if commit:
            instance.save()
        return instance


class CompanyRelationForm(forms.ModelForm):
    """Validate related choices against the object's company, including POSTs."""
    class Meta:
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        company_id = self.instance.company_id if getattr(self.instance, "company_id", None) else self.initial.get("company")
        if self.is_bound:
            company_id = self.data.get(self.add_prefix("company"), company_id)
        if not company_id:
            return
        for name, field in self.fields.items():
            queryset = getattr(field, "queryset", None)
            if queryset is not None and any(f.name == "company" for f in queryset.model._meta.fields):
                try:
                    field.queryset = queryset.filter(company_id=company_id)
                except (ValidationError, ValueError):
                    field.queryset = queryset.none()
        if "status" in self.fields and isinstance(self.instance, (Appointment, Task)):
            entity = "appointment" if isinstance(self.instance, Appointment) else "task"
            self.fields["status"].queryset = self.fields["status"].queryset.filter(entity_type=entity)

    def clean(self):
        data = super().clean()
        company = data.get("company") or getattr(self.instance, "company", None)
        if company:
            for name in ("roles", "assignees", "resources"):
                items = data.get(name)
                if items is not None and any(item.company_id != company.pk for item in items):
                    self.add_error(name, "Выберите объекты из той же компании.")
        if isinstance(self.instance, Appointment) and not data.get("cancelled_at") and all(data.get(name) for name in ("company","employee","starts_at","ends_at")):
            if data["starts_at"] < data["ends_at"]:
                try:
                    # Admin change forms already run inside an outer transaction.
                    with transaction.atomic():
                        employee_ids = [data["employee"].pk]
                        if self.instance.employee_id:
                            employee_ids.append(self.instance.employee_id)
                        resources = list(data.get("resources", []))
                        if not self.instance._state.adding:
                            resources.extend(self.instance.resources.all())
                        list(Membership.objects.filter(pk__in=employee_ids).order_by("pk").select_for_update())
                        list(Resource.objects.filter(pk__in=[item.pk for item in resources]).order_by("pk").select_for_update())
                        validate_window(data["company"],data["employee"],data["starts_at"],data["ends_at"],data.get("resources",[]),self.instance.pk)
                except ValidationError as error:
                    for field, messages in error.message_dict.items():
                        self.add_error(field,messages)
        return data
