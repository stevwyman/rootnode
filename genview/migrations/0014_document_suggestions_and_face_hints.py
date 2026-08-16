# Generated manually for document intelligence + face suggestions

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("genview", "0013_tree_starting_individual"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mediaobject",
            name="extracted_text",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="facetag",
            name="match_distance",
            field=models.FloatField(
                blank=True,
                help_text="Kosinus-Abstand zum besten Embedding-Match (kleiner = ähnlicher).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="facetag",
            name="suggested_individual",
            field=models.ForeignKey(
                blank=True,
                help_text="Automatisch vorgeschlagene Person (noch nicht bestätigt).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="suggested_face_tags",
                to="genview.individual",
            ),
        ),
        migrations.CreateModel(
            name="DocumentExtractionSuggestion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Ausstehend"),
                            ("accepted", "Übernommen"),
                            ("rejected", "Abgelehnt"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("event_type_tag", models.CharField(max_length=4)),
                ("person_name", models.CharField(blank=True, max_length=255)),
                ("raw_date", models.CharField(blank=True, max_length=100)),
                ("parsed_date", models.DateField(blank=True, null=True)),
                ("place_name", models.CharField(blank=True, max_length=255)),
                ("context_line", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_event",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_document_suggestions",
                        to="genview.event",
                    ),
                ),
                (
                    "individual",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="document_suggestions",
                        to="genview.individual",
                    ),
                ),
                (
                    "media",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_suggestions",
                        to="genview.mediaobject",
                    ),
                ),
                (
                    "place",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="document_suggestions",
                        to="genview.place",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
