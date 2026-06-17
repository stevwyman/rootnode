import os
from django.conf import settings

def app_version(request):
    """Reads the version.txt file and makes it available to all templates."""
    version_file = os.path.join(settings.BASE_DIR, 'version.txt')
    try:
        with open(version_file, 'r') as f:
            version = f.read().strip()
    except FileNotFoundError:
        version = "Dev-Build"  # Fallback if the file doesn't exist yet
        
    return {'APP_VERSION': version}