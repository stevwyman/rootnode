from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("genview", "0014_document_suggestions_and_face_hints"),
    ]

    operations = [
        migrations.AddField(
            model_name="tree",
            name="show_living_people",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Wenn aktiviert, gelten auf einem öffentlichen Baum keine "
                    "Lebend-Datenschutzregeln für Gäste. Editoren und Admins "
                    "sehen lebende Personen immer. Mitglieder eines privaten "
                    "Baums ebenfalls."
                ),
                verbose_name="Lebende Personen anzeigen",
            ),
        ),
    ]
