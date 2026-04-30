# Generated migration - HSNCode and GSTRate models

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='HSNCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(db_index=True, max_length=8, unique=True)),
                ('description', models.CharField(max_length=500)),
                ('category', models.CharField(blank=True, max_length=100)),
                ('slug', models.SlugField(db_index=True, max_length=150, unique=True)),
                ('meta_title', models.CharField(blank=True, max_length=200)),
                ('meta_description', models.CharField(blank=True, max_length=300)),
                ('long_description', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('external_id', models.CharField(blank=True, max_length=100, null=True)),
            ],
            options={
                'verbose_name': 'HSN Code',
                'verbose_name_plural': 'HSN Codes',
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='GSTRate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(db_index=True, max_length=200)),
                ('hsn_codes', models.CharField(blank=True, help_text='Comma-separated HSN codes', max_length=500)),
                ('rate', models.IntegerField(choices=[(0, '0%'), (5, '5%'), (12, '12%'), (18, '18%'), (28, '28%')])),
                ('slug', models.SlugField(db_index=True, max_length=150, unique=True)),
                ('meta_title', models.CharField(blank=True, max_length=200)),
                ('meta_description', models.CharField(blank=True, max_length=300)),
                ('long_description', models.TextField(blank=True)),
                ('effective_from', models.DateField(blank=True, null=True)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('external_id', models.CharField(blank=True, max_length=100, null=True)),
            ],
            options={
                'verbose_name': 'GST Rate',
                'verbose_name_plural': 'GST Rates',
                'ordering': ['rate', 'category'],
                'unique_together': {('category', 'rate')},
            },
        ),
        migrations.AddIndex(
            model_name='hsncode',
            index=models.Index(fields=['code'], name='references_hsnco_code_idx'),
        ),
        migrations.AddIndex(
            model_name='hsncode',
            index=models.Index(fields=['slug'], name='references_hsnco_slug_idx'),
        ),
        migrations.AddIndex(
            model_name='hsncode',
            index=models.Index(fields=['category'], name='references_hsnco_categ_idx'),
        ),
        migrations.AddIndex(
            model_name='gstrate',
            index=models.Index(fields=['rate'], name='references_gstrat_rate_idx'),
        ),
        migrations.AddIndex(
            model_name='gstrate',
            index=models.Index(fields=['category'], name='references_gstrat_categ_idx'),
        ),
        migrations.AddIndex(
            model_name='gstrate',
            index=models.Index(fields=['slug'], name='references_gstrat_slug_idx'),
        ),
    ]
