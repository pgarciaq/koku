# Generated by Django 2.1.5 on 2019-02-05 20:22

import django.contrib.postgres.fields.jsonb
import django.contrib.postgres.indexes
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('reporting', '0027_auto_20190205_1659'),
    ]

    operations = [
        migrations.CreateModel(
            name='OCPAWSCostLineItemDaily',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cluster_id', models.CharField(max_length=50, null=True)),
                ('cluster_alias', models.CharField(max_length=256, null=True)),
                ('namespace', models.CharField(max_length=253)),
                ('pod', models.CharField(max_length=253)),
                ('node', models.CharField(max_length=253)),
                ('resource_id', models.CharField(max_length=253, null=True)),
                ('usage_start', models.DateTimeField()),
                ('usage_end', models.DateTimeField()),
                ('pod_usage_cpu_core_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('pod_request_cpu_core_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('pod_limit_cpu_core_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('pod_usage_memory_byte_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('pod_request_memory_byte_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('pod_limit_memory_byte_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('node_capacity_cpu_cores', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('node_capacity_cpu_core_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('node_capacity_memory_bytes', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('node_capacity_memory_byte_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('cluster_capacity_cpu_core_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('cluster_capacity_memory_byte_seconds', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('total_seconds', models.IntegerField()),
                ('pod_labels', django.contrib.postgres.fields.jsonb.JSONField(null=True)),
                ('line_item_type', models.CharField(max_length=50)),
                ('usage_account_id', models.CharField(max_length=50)),
                ('product_code', models.CharField(max_length=50)),
                ('usage_type', models.CharField(max_length=50, null=True)),
                ('operation', models.CharField(max_length=50, null=True)),
                ('availability_zone', models.CharField(max_length=50, null=True)),
                ('usage_amount', models.FloatField(null=True)),
                ('normalization_factor', models.FloatField(null=True)),
                ('normalized_usage_amount', models.FloatField(null=True)),
                ('currency_code', models.CharField(max_length=10)),
                ('unblended_rate', models.DecimalField(decimal_places=9, max_digits=17, null=True)),
                ('unblended_cost', models.DecimalField(decimal_places=9, max_digits=17, null=True)),
                ('blended_rate', models.DecimalField(decimal_places=9, max_digits=17, null=True)),
                ('blended_cost', models.DecimalField(decimal_places=9, max_digits=17, null=True)),
                ('public_on_demand_cost', models.DecimalField(decimal_places=9, max_digits=17, null=True)),
                ('public_on_demand_rate', models.DecimalField(decimal_places=9, max_digits=17, null=True)),
                ('tax_type', models.TextField(null=True)),
                ('tags', django.contrib.postgres.fields.jsonb.JSONField(null=True)),
            ],
            options={
                'db_table': 'reporting_ocpawscostlineitem_daily',
                'managed': False,
            },
        ),
        migrations.CreateModel(
            name='OCPAWSCostLineItemDailySummary',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cluster_id', models.CharField(max_length=50, null=True)),
                ('cluster_alias', models.CharField(max_length=256, null=True)),
                ('namespace', models.CharField(max_length=253)),
                ('pod', models.CharField(max_length=253)),
                ('node', models.CharField(max_length=253)),
                ('resource_id', models.CharField(max_length=253, null=True)),
                ('usage_start', models.DateTimeField()),
                ('usage_end', models.DateTimeField()),
                ('pod_labels', django.contrib.postgres.fields.jsonb.JSONField(null=True)),
                ('product_code', models.CharField(max_length=50)),
                ('product_family', models.CharField(max_length=150, null=True)),
                ('usage_account_id', models.CharField(max_length=50)),
                ('availability_zone', models.CharField(max_length=50, null=True)),
                ('region', models.CharField(max_length=50, null=True)),
                ('unit', models.CharField(max_length=63, null=True)),
                ('tags', django.contrib.postgres.fields.jsonb.JSONField(null=True)),
                ('unblended_cost', models.DecimalField(decimal_places=9, max_digits=17, null=True)),
                ('pod_cost', models.DecimalField(decimal_places=6, max_digits=24, null=True)),
                ('account_alias', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='reporting.AWSAccountAlias')),
            ],
            options={
                'db_table': 'reporting_ocpawscostlineitem_daily_summary',
            },
        ),
        migrations.AddIndex(
            model_name='ocpawscostlineitemdailysummary',
            index=models.Index(fields=['usage_start'], name='cost_summary_ocp_usage_idx'),
        ),
        migrations.AddIndex(
            model_name='ocpawscostlineitemdailysummary',
            index=models.Index(fields=['namespace'], name='cost_summary_namespace_idx'),
        ),
        migrations.AddIndex(
            model_name='ocpawscostlineitemdailysummary',
            index=models.Index(fields=['node'], name='cost_summary_node_idx'),
        ),
        migrations.AddIndex(
            model_name='ocpawscostlineitemdailysummary',
            index=django.contrib.postgres.indexes.GinIndex(fields=['pod_labels'], name='cost_pod_labels_idx'),
        ),
    ]
