from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext_lazy as _

APP_SETTINGS_CACHE_KEY = "core.app_settings"


class AppSettings(models.Model):
    """Singleton row for site-wide feature switches."""

    enable_tree_query = models.BooleanField(
        default=True,
        verbose_name=_("Stammbaum fragen"),
        help_text=_(
            "Natürliche Sprache und strukturierte Abfragen am Stammbaum. "
            "Unklare Fragen können ein Sprachmodell (Ollama) aufrufen."
        ),
    )
    enable_ocr = models.BooleanField(
        default=True,
        verbose_name=_("OCR / Texterkennung"),
        help_text=_(
            "Text aus Dokumenten extrahieren und Ereignis-Vorschläge erzeugen. "
            "Benötigt den OCR-Dienst (textnode)."
        ),
    )
    enable_face_recognition = models.BooleanField(
        default=True,
        verbose_name=_("Gesichtserkennung"),
        help_text=_(
            "Gesichter auf Fotos erkennen und Personen vorschlagen. "
            "Benötigt den Gesichtsdienst (facenode)."
        ),
    )

    class Meta:
        verbose_name = _("Anwendungseinstellungen")
        verbose_name_plural = _("Anwendungseinstellungen")

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(APP_SETTINGS_CACHE_KEY)

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete(APP_SETTINGS_CACHE_KEY)

    def __str__(self):
        return str(_("Anwendungseinstellungen"))
