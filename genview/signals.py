# genview/signals.py
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver
from .models import MediaObject

from .utils import generate_thumbnail_for_instance


@receiver(m2m_changed, sender=MediaObject.individuals.through)
def ensure_single_portrait(sender, instance, action, pk_set, **kwargs):
    """
    Wird ausgelöst, wenn Personen zu einem MediaObject hinzugefügt/entfernt werden.
    Wenn `instance.is_portrait` True ist, entfernen wir das Portrait-Flag
    von allen anderen MediaObjects dieser Personen.
    """
    # 🔥 NEU: Wenn das Signal von der Person aus getriggert wird (reverse=True), 
    # brechen wir ab. 'instance' ist hier nämlich die Person, nicht das Bild!
    if kwargs.get('reverse', False):
        return
    
    if action == "post_add" and instance.is_portrait:
        # Für jede neu verknüpfte Person das Flag bei anderen MediaObjects zurücksetzen
        for person_id in pk_set:
            MediaObject.objects.filter(
                individuals__pk=person_id, is_portrait=True
            ).exclude(pk=instance.pk).update(is_portrait=False)


@receiver(post_save, sender=MediaObject)
def portrait_cleanup_on_save(sender, instance, created, **kwargs):
    """
    Falls das Portrait-Flag manuell (z. B. im Admin) gesetzt wird,
    stellen wir sicher, dass kein zweites Portrait für dieselbe Person existiert.
    """
    if instance.is_portrait:
        for person in instance.individuals.all():
            MediaObject.objects.filter(individuals=person, is_portrait=True).exclude(
                pk=instance.pk
            ).update(is_portrait=False)


@receiver(post_save, sender=MediaObject)
def create_thumbnails_on_save(sender, instance, created, **kwargs):
    """
    When a MediaObject is created or its file is changed, generate both
    thumbnails (mini & small). Also backfills missing thumbnails on save.
    """
    if not instance.file:
        return

    # Avoid recursion when generate_thumbnail_for_instance() saves thumb_* fields.
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and set(update_fields).issubset({"thumb_mini", "thumb_small"}):
        return

    file_changed = False
    get_dirty_fields = getattr(instance, "get_dirty_fields", None)
    if callable(get_dirty_fields):
        file_changed = "file" in get_dirty_fields()

    for size in ("mini", "small"):
        thumb_field = getattr(instance, f"thumb_{size}")
        missing = not thumb_field or not thumb_field.name
        if not (created or file_changed or missing):
            continue
        try:
            generate_thumbnail_for_instance(instance, size)
        except Exception as exc:
            print(f"Thumbnail generation failed for {instance.id} ({size}): {exc}")
