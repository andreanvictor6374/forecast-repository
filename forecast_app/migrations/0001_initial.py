# -*- coding: utf-8 -*-
# Generated by Django 1.11 on 2018-05-31 19:07
from __future__ import unicode_literals

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import jsonfield.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Forecast',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('csv_filename', models.CharField(help_text="CSV file name of this forecast's data source.", max_length=200)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ForecastData',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('location', models.CharField(max_length=200)),
                ('target', models.CharField(max_length=200)),
                ('row_type', models.CharField(choices=[('point', 'Point'), ('bin', 'Bin')], max_length=5)),
                ('unit', models.CharField(max_length=200)),
                ('bin_start_incl', models.FloatField(null=True)),
                ('bin_end_notincl', models.FloatField(null=True)),
                ('value', models.FloatField(null=True)),
                ('forecast', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cdcdata_set', to='forecast_app.Forecast')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ForecastModel',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.CharField(help_text='A few paragraphs describing the model. Please see documentation forwhat should be included here - information on reproducing the model’s results, etc.', max_length=2000)),
                ('home_url', models.URLField(help_text="The model's home site.")),
                ('aux_data_url', models.URLField(blank=True, help_text='Optional model-specific auxiliary data directory or Zip file containing data files (e.g., CSV files) beyond Project.core_data that were used by this model.', null=True)),
                ('owner', models.ForeignKey(blank=True, help_text="The model's owner.", null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('csv_filename', models.CharField(help_text="CSV file name of this project's template file.", max_length=200)),
                ('is_public', models.BooleanField(default=True, help_text="Controls project visibility. False means the project is private and can only be accessed by the project's owner or any of its model_owners. True means it is publicly accessible.")),
                ('name', models.CharField(max_length=200)),
                ('time_interval_type', models.CharField(choices=[('w', 'Week'), ('b', 'Biweek'), ('m', 'Month')], default='w', max_length=1)),
                ('truth_csv_filename', models.CharField(help_text='Name of the truth csv file that was uploaded.', max_length=200)),
                ('description', models.CharField(help_text="A few paragraphs describing the project. Please see documentation forwhat should be included here - 'real-time-ness', time_zeros, etc.", max_length=2000)),
                ('home_url', models.URLField(help_text="The project's home site.")),
                ('logo_url', models.URLField(blank=True, help_text="The project's optional logo image.", null=True)),
                ('core_data', models.URLField(help_text='Directory or Zip file containing data files (e.g., CSV files) made made available to everyone in the challenge, including supplemental data like Google queries or weather.')),
                ('config_dict', jsonfield.fields.JSONField(blank=True, help_text="JSON dict containing these keys: 'visualization-y-label'. Please see documentation for details.", null=True)),
                ('model_owners', models.ManyToManyField(blank=True, help_text='Users who are allowed to create, edit, and delete ForecastModels in this project. Or: non-editing users who simply need access to a private project. Use control/command click to add/remove from the list. ', to=settings.AUTH_USER_MODEL)),
                ('owner', models.ForeignKey(blank=True, help_text="The project's owner.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_owner', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ProjectTemplateData',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('location', models.CharField(max_length=200)),
                ('target', models.CharField(max_length=200)),
                ('row_type', models.CharField(choices=[('point', 'Point'), ('bin', 'Bin')], max_length=5)),
                ('unit', models.CharField(max_length=200)),
                ('bin_start_incl', models.FloatField(null=True)),
                ('bin_end_notincl', models.FloatField(null=True)),
                ('value', models.FloatField(null=True)),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cdcdata_set', to='forecast_app.Project')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Target',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.CharField(help_text='A few paragraphs describing the target.', max_length=2000)),
                ('is_step_ahead', models.BooleanField(default=False, help_text="Flag that's True if this Target is a 'k-step-ahead' one that can be used in analysis tools to reference forward and back in a Project's TimeZeros (when sorted by timezero_date). If True then step_ahead_increment must be set. Default is False.")),
                ('step_ahead_increment', models.IntegerField(default=0, help_text="Optional field that's required when Target.is_step_ahead is True, is an integer specifing how many time steps ahead the Target is. Can be negative, zero, or positive.")),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='targets', to='forecast_app.Project')),
            ],
        ),
        migrations.CreateModel(
            name='TimeZero',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timezero_date', models.DateField(help_text='A date that a target is relative to.')),
                ('data_version_date', models.DateField(blank=True, help_text='The optional database date at which models should work with for the timezero_date.', null=True)),
                ('is_season_start', models.BooleanField(default=False, help_text='True if this TimeZero starts a season.')),
                ('season_name', models.CharField(blank=True, help_text='The name of the season this TimeZero starts, if is_season_start.', max_length=50, null=True)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='timezeros', to='forecast_app.Project')),
            ],
        ),
        migrations.CreateModel(
            name='TruthData',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('location', models.CharField(max_length=200)),
                ('target', models.CharField(max_length=200)),
                ('value', models.FloatField(null=True)),
                ('time_zero', models.ForeignKey(help_text='Project TimeZero that this data point relates to.', on_delete=django.db.models.deletion.CASCADE, to='forecast_app.TimeZero')),
            ],
        ),
        migrations.AddField(
            model_name='forecastmodel',
            name='project',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='models', to='forecast_app.Project'),
        ),
        migrations.AddField(
            model_name='forecast',
            name='forecast_model',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='forecasts', to='forecast_app.ForecastModel'),
        ),
        migrations.AddField(
            model_name='forecast',
            name='time_zero',
            field=models.ForeignKey(help_text='TimeZero that this forecast is in relation to.', on_delete=django.db.models.deletion.CASCADE, to='forecast_app.TimeZero'),
        ),
        migrations.AddIndex(
            model_name='projecttemplatedata',
            index=models.Index(fields=['location'], name='forecast_ap_locatio_425605_idx'),
        ),
        migrations.AddIndex(
            model_name='projecttemplatedata',
            index=models.Index(fields=['target'], name='forecast_ap_target_5c89be_idx'),
        ),
        migrations.AddIndex(
            model_name='projecttemplatedata',
            index=models.Index(fields=['row_type'], name='forecast_ap_row_typ_92553d_idx'),
        ),
        migrations.AddIndex(
            model_name='projecttemplatedata',
            index=models.Index(fields=['unit'], name='forecast_ap_unit_44ae87_idx'),
        ),
        migrations.AddIndex(
            model_name='forecastdata',
            index=models.Index(fields=['location'], name='forecast_ap_locatio_5333b9_idx'),
        ),
        migrations.AddIndex(
            model_name='forecastdata',
            index=models.Index(fields=['target'], name='forecast_ap_target_6720fe_idx'),
        ),
        migrations.AddIndex(
            model_name='forecastdata',
            index=models.Index(fields=['row_type'], name='forecast_ap_row_typ_160dd7_idx'),
        ),
        migrations.AddIndex(
            model_name='forecastdata',
            index=models.Index(fields=['unit'], name='forecast_ap_unit_a95037_idx'),
        ),
    ]
