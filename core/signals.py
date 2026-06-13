# core/signals.py
import os
import logging
from django.conf import settings
from django.db.models.signals import post_migrate
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.db import IntegrityError

logger = logging.getLogger(__name__)

_already_created = False

@receiver(post_migrate)
def create_default_superuser(sender, **kwargs):
    """
    Legt einen Super‑User an, wenn er noch nicht existiert.
    Der Handler wird nach jedem erfolgreichen `migrate`‑Durchlauf ausgelöst,
    also erst **nach** dem Anlegen aller Tabellen.
    """
    # Wir wollen das nur in Entwicklungs‑/Staging‑Umgebungen ausführen.
    if not getattr(settings, "CREATE_SUPERUSER_ON_STARTUP", False):
        return
    
    global _already_created
    if _already_created:
        return
    _already_created = True          # nur beim ersten Aufruf weiterfahren

    User = get_user_model()
    username = os.getenv("DJANGO_SUPERUSER_USERNAME", "admin")
    email    = os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
    password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "changeme")

    try:
        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username=username,
                                         email=email,
                                         password=password)
            logger.info(f"Super‑User '{username}' wurde angelegt.")
        else:
            logger.debug(f"Super‑User '{username}' existiert bereits.")
    except IntegrityError:
        # Race‑Condition – ein anderer Prozess hat den User bereits erstellt.
        logger.debug("Super‑User bereits von einem anderen Prozess angelegt.")