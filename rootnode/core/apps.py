from django.apps import AppConfig
from dotenv import load_dotenv
from logging import getLogger
import os

logger = getLogger(__name__)

load_dotenv()

class CoreConfig(AppConfig):
    name = 'core'                     # <-- App‑Name
    verbose_name = "Core utilities"

    def ready(self):
        """
        Wird ausgeführt, sobald Django das App‑Registry aufgebaut hat.
        Wir legen hier den Super‑User an, falls er noch nicht existiert.
        """
        # Wir wollen das nur einmal pro Prozess ausführen (z. B. nicht bei `manage.py migrate`).
        # Deshalb prüfen wir, ob das DB‑Backend bereits bereit ist.
        from django.conf import settings
        if not settings.configured:
            return

        # Das ganze nur in Entwicklungs‑/Staging‑Umgebungen ausführen
        if not getattr(settings, "CREATE_SUPERUSER_ON_STARTUP", False):
            return

        from django.contrib.auth import get_user_model
        from django.db import OperationalError, IntegrityError, ProgrammingError

        User = get_user_model()

        # Werte aus Settings (oder Umgebungsvariablen) – sicherer als Hard‑Code.
        username = os.getenv('DJANGO_SUPERUSER_USERNAME', os.getenv('DJANGO_SUPERUSER_USERNAME'))
        email    = os.getenv('DJANGO_SUPERUSER_EMAIL', os.getenv('DJANGO_SUPERUSER_EMAIL'))
        password = os.getenv('DJANGO_SUPERUSER_PASSWORD', os.getenv('DJANGO_SUPERUSER_PASSWORD'))

        try:
            if not User.objects.filter(username=username).exists():
                User.objects.create_superuser(username=username,
                                             email=email,
                                             password=password)
                logger.info(f"Super‑User '{username}' wurde angelegt.")
            else:
                logger.debug(f"Super‑User '{username}' existiert bereits.")
        except (OperationalError, ProgrammingError) as exc:
            # Während `migrate` ist die DB evtl. noch nicht bereit – einfach ignorieren.
            logger.debug("Datenbank noch nicht bereit – Super‑User‑Erstellung übersprungen.")
        except IntegrityError:
            # Race‑Condition: ein anderer Prozess hat den User bereits angelegt.
            logger.debug("Super‑User bereits von anderem Prozess erstellt.")