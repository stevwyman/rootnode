# core/apps.py
from django.apps import AppConfig

class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        # Importiert das Signal-Modul – das registriert den @receiver
        import core.signals   # noqa: F401