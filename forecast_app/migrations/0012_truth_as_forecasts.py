# Generated by Django 2.2.15 on 2020-12-16 13:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('forecast_app', '0011_delete_rowcountcache'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='project',
            name='truth_csv_filename',
        ),
        migrations.RemoveField(
            model_name='project',
            name='truth_updated_at',
        ),
        migrations.AddField(
            model_name='forecastmodel',
            name='is_oracle',
            field=models.BooleanField(default=False, help_text='True if this model acts as a truth oracle.'),
        ),
        migrations.DeleteModel(
            name='TruthData',
        ),
    ]
