from django.contrib import admin

from .models import AppSettings


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "enable_tree_query",
        "enable_ocr",
        "enable_face_recognition",
        "enable_colorize",
    )

    def has_add_permission(self, request):
        return not AppSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
