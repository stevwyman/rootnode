# genview/signals.py
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver
from .models import MediaObject, Individual

@receiver(m2m_changed, sender=MediaObject.individuals.through)
def ensure_single_portrait(sender, instance, action, pk_set, **kwargs):
    """
    Wird ausgelöst, wenn Personen zu einem MediaObject hinzugefügt/entfernt werden.
    Wenn `instance.is_portrait` True ist, entfernen wir das Portrait‑Flag
    von allen anderen MediaObjects dieser Personen.
    """
    if action == "post_add" and instance.is_portrait:
        # Für jede neu verknüpfte Person das Flag bei anderen MediaObjects zurücksetzen
        for person_id in pk_set:
            MediaObject.objects.filter(
                individuals__pk=person_id,
                is_portrait=True
            ).exclude(pk=instance.pk).update(is_portrait=False)


@receiver(post_save, sender=MediaObject)
def portrait_cleanup_on_save(sender, instance, created, **kwargs):
    """
    Falls das Portrait‑Flag manuell (z. B. im Admin) gesetzt wird,
    stellen wir sicher, dass kein zweites Portrait für dieselbe Person existiert.
    """
    if instance.is_portrait:
        for person in instance.individuals.all():
            MediaObject.objects.filter(
                individuals=person,
                is_portrait=True
            ).exclude(pk=instance.pk).update(is_portrait=False)