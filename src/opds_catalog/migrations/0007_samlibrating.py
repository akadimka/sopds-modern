from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("opds_catalog", "0006_alter_author_id_alter_bauthor_id_alter_bgenre_id_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SamlibRating",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rating", models.FloatField(blank=True, null=True)),
                ("votes", models.IntegerField(default=0)),
                ("samlib_url", models.CharField(blank=True, max_length=512)),
                ("fetched_at", models.DateTimeField(blank=True, null=True)),
                (
                    "book",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="samlib_rating",
                        to="opds_catalog.book",
                    ),
                ),
            ],
            options={
                "app_label": "opds_catalog",
            },
        ),
    ]
