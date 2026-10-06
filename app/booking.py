from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import Appointment, TimeOff, WorkSchedule


def validate_window(company, employee, starts_at, ends_at, resources, instance_id=None):
    """Apply identical availability rules in the API and Django admin."""
    local_start = timezone.localtime(starts_at, ZoneInfo(company.timezone))
    local_end = timezone.localtime(ends_at, ZoneInfo(company.timezone))
    available = WorkSchedule.objects.filter(company=company, employee=employee, weekday=local_start.weekday(),
        is_active=True, start_time__lte=local_start.time().replace(tzinfo=None), end_time__gte=local_end.time().replace(tzinfo=None)
    ).filter(Q(valid_from__isnull=True) | Q(valid_from__lte=local_start.date())).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=local_start.date())).exists()
    if local_start.date() != local_end.date() or not available:
        raise ValidationError({"starts_at": "Время находится вне рабочего графика сотрудника."})
    if TimeOff.objects.filter(company=company, employee=employee, is_approved=True, starts_at__lt=ends_at, ends_at__gt=starts_at).exists():
        raise ValidationError({"starts_at": "На это время у сотрудника запланировано отсутствие."})
    if resources:
        overlaps = Appointment.objects.filter(company=company, resources__in=resources, starts_at__lt=ends_at, ends_at__gt=starts_at, cancelled_at__isnull=True)
        if instance_id:
            overlaps = overlaps.exclude(pk=instance_id)
        if overlaps.exists():
            raise ValidationError({"resources": "Один из ресурсов уже занят в это время."})
