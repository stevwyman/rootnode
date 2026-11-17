from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_enable_colorize"),
    ]

    operations = [
        migrations.AddField(
            model_name="appsettings",
            name="enable_stories",
            field=models.BooleanField(
                default=True,
                help_text="Erzählungen, Ortsgeschichte und Suchhinweise mit Verknüpfungen zu Personen, Familien und Orten.",
                verbose_name="Geschichten",
            ),
        ),
    ]
