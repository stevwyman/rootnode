from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("genview", "0015_tree_show_living_people"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="individual",
            name="genview_ind_surname_e30eda_idx",
        ),
        migrations.RemoveIndex(
            model_name="individual",
            name="genview_ind_sex_eaac70_idx",
        ),
        migrations.RemoveIndex(
            model_name="childfamilylink",
            name="genview_chi_child_i_00c802_idx",
        ),
        migrations.AddIndex(
            model_name="individual",
            index=models.Index(
                fields=["gedcom_tree", "surname", "given_name"],
                name="genview_ind_tree_name_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                fields=["gedcom_tree", "event_type", "parsed_date"],
                name="genview_eve_tree_type_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                fields=["gedcom_tree", "parsed_date"],
                name="genview_eve_tree_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="event",
            index=models.Index(
                fields=["place", "parsed_date"],
                name="genview_eve_place_date_idx",
            ),
        ),
    ]
