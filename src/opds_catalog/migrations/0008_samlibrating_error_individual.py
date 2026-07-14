from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("opds_catalog", "0007_samlibrating"),
    ]

    operations = [
        migrations.AddField(
            model_name="samlibrating",
            name="fetch_error",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="samlibrating",
            name="individual_ratings",
            field=models.TextField(blank=True, default=""),
        ),
    ]
