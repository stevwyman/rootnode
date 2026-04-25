from django.apps import AppConfig


class GenviewConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'genview'

    def ready(self):
        # Importiert das signals‑Modul, damit die Signal‑Handler registriert werden
        import genview.signals