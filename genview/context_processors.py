import os
from django.conf import settings

from core.features import get_app_settings


def app_version(request):
    """Reads the version.txt file and makes it available to all templates."""
    version_file = os.path.join(settings.BASE_DIR, 'version.txt')
    try:
        with open(version_file, 'r') as f:
            version = f.read().strip()
    except FileNotFoundError:
        version = "Dev-Build"  # Fallback if the file doesn't exist yet
        
    return {'APP_VERSION': version}


def app_features(request):
    """Expose site-wide feature flags to every template."""
    flags = get_app_settings()
    return {
        "enable_tree_query": flags.enable_tree_query,
        "enable_ocr": flags.enable_ocr,
        "enable_face_recognition": flags.enable_face_recognition,
        "enable_colorize": flags.enable_colorize,
    }