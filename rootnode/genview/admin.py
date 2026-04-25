# gedcom_app/admin.py
from django.contrib import admin
from .models import Individual, Family, Event, Source, MediaObject, ChildFamilyLink

admin.site.register([Individual, Family, Event, Source, ChildFamilyLink])

class IsImageFilter(admin.SimpleListFilter):
    title = 'Bildtyp'            # Anzeigename im Admin
    parameter_name = 'is_image'  # URL‑Parameter

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Nur Bilder'),
            ('no',  'Nur Nicht‑Bilder'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'yes':
            return queryset.filter(file__iendswith=('.png', '.jpg', '.jpeg', '.gif', '.webp'))
        if value == 'no':
            return queryset.exclude(file__iendswith=('.png', '.jpg', '.jpeg', '.gif', '.webp'))
        return queryset

@admin.register(MediaObject)
class MediaObjectAdmin(admin.ModelAdmin):
    list_display = ("title", "is_image", "is_portrait")
    list_filter = ("is_portrait",IsImageFilter)
    search_fields = ("title", "gedcom_id")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        # Identische Logik wie im Form‑save(): andere Portraits zurücksetzen
        if obj.is_portrait:
            for person in obj.individuals.all():
                MediaObject.objects.filter(
                    individuals=person,
                    is_portrait=True
                ).exclude(pk=obj.pk).update(is_portrait=False)