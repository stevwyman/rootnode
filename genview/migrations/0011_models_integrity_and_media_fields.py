# Generated manually for model integrity and field fixes

from django.db import migrations, models
import genview.models


class Migration(migrations.Migration):

    dependencies = [
        ("genview", "0010_mediaobject_extracted_text"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "UPDATE genview_alternativename SET given_name = '' WHERE given_name IS NULL; "
                "UPDATE genview_alternativename SET surname = '' WHERE surname IS NULL;"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="mediaobject",
            name="extracted_text",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="mediaobject",
            name="file",
            field=models.FileField(
                blank=True,
                help_text="May be empty for GEDCOM import placeholders awaiting bulk upload.",
                upload_to=genview.models.tree_media_directory_path,
                verbose_name="Datei/Bild",
            ),
        ),
        migrations.AlterField(
            model_name="alternativename",
            name="given_name",
            field=models.CharField(
                blank=True, default="", max_length=100, verbose_name="Vorname"
            ),
        ),
        migrations.AlterField(
            model_name="alternativename",
            name="surname",
            field=models.CharField(
                blank=True, default="", max_length=100, verbose_name="Nachname"
            ),
        ),
    ]
