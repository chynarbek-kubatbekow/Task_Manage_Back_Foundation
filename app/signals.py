from datetime import time

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Membership, WorkSchedule


@receiver(post_save, sender=Membership)
def create_default_work_schedule(sender, instance, created, **kwargs):
    if not created:
        return
    WorkSchedule.objects.bulk_create([
        WorkSchedule(
            company=instance.company,
            employee=instance,
            weekday=weekday,
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        for weekday in range(5)
    ])
