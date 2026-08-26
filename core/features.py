"""Site-wide feature flags stored in the AppSettings singleton."""

from __future__ import annotations

import sys

from django.core.cache import cache
from django.db.utils import OperationalError, ProgrammingError

from .models import APP_SETTINGS_CACHE_KEY, AppSettings

FEATURE_TREE_QUERY = "tree_query"
FEATURE_OCR = "ocr"
FEATURE_FACE_RECOGNITION = "face_recognition"
FEATURE_COLORIZE = "colorize"

_FEATURE_FIELDS = {
    FEATURE_TREE_QUERY: "enable_tree_query",
    FEATURE_OCR: "enable_ocr",
    FEATURE_FACE_RECOGNITION: "enable_face_recognition",
    FEATURE_COLORIZE: "enable_colorize",
}

_CACHE_TTL = 60


def get_app_settings() -> AppSettings:
    use_cache = "test" not in sys.argv
    if use_cache:
        cached = cache.get(APP_SETTINGS_CACHE_KEY)
        if cached is not None:
            return cached
    try:
        obj, _created = AppSettings.objects.get_or_create(pk=1)
    except (OperationalError, ProgrammingError):
        return AppSettings(
            pk=1,
            enable_tree_query=True,
            enable_ocr=True,
            enable_face_recognition=True,
            enable_colorize=False,
        )
    # Tests roll back the DB without calling save(), which would otherwise
    # leave a stale cached row. Skip the cache under the test runner.
    if use_cache:
        cache.set(APP_SETTINGS_CACHE_KEY, obj, _CACHE_TTL)
    return obj


def feature_enabled(name: str) -> bool:
    field = _FEATURE_FIELDS.get(name)
    if not field:
        return False
    return bool(getattr(get_app_settings(), field, False))


def clear_app_settings_cache() -> None:
    cache.delete(APP_SETTINGS_CACHE_KEY)
