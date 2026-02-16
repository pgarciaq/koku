#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
import django.db.models.deletion
from django.db import migrations
from django.db import models

from koku.database import set_pg_extended_mode
from koku.database import unset_pg_extended_mode


class Migration(migrations.Migration):

    dependencies = [
        ("reporting", "0344_add_cost_model_rate_name"),
    ]

    operations = [
        migrations.RunPython(code=set_pg_extended_mode, reverse_code=unset_pg_extended_mode),
        migrations.CreateModel(
            name="OCPCostBreakdownP",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("cluster_id", models.TextField()),
                ("cluster_alias", models.TextField(null=True)),
                ("usage_start", models.DateField()),
                ("usage_end", models.DateField()),
                ("cost_model_cpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_memory_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_volume_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_gpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_rate_type", models.TextField(null=True)),
                ("cost_model_rate_name", models.TextField(null=True)),
                ("infrastructure_raw_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("infrastructure_markup_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("raw_currency", models.TextField(null=True)),
                ("distributed_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                (
                    "cost_category",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.CASCADE, to="reporting.openshiftcostcategory"
                    ),
                ),
                (
                    "source_uuid",
                    models.ForeignKey(
                        db_column="source_uuid",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="reporting.tenantapiprovider",
                    ),
                ),
            ],
            options={
                "db_table": "reporting_ocp_cost_breakdown_p",
            },
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownp",
            index=models.Index(fields=["usage_start"], name="ocp_brkdwn_usage_start"),
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownp",
            index=models.Index(fields=["cost_model_rate_name"], name="ocp_brkdwn_rate_name"),
        ),
        migrations.CreateModel(
            name="OCPCostBreakdownByProjectP",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("cluster_id", models.TextField()),
                ("cluster_alias", models.TextField(null=True)),
                ("namespace", models.CharField(max_length=253, null=True)),
                ("usage_start", models.DateField()),
                ("usage_end", models.DateField()),
                ("cost_model_cpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_memory_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_volume_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_gpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_rate_type", models.TextField(null=True)),
                ("cost_model_rate_name", models.TextField(null=True)),
                ("infrastructure_raw_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("infrastructure_markup_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("raw_currency", models.TextField(null=True)),
                ("distributed_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                (
                    "cost_category",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.CASCADE, to="reporting.openshiftcostcategory"
                    ),
                ),
                (
                    "source_uuid",
                    models.ForeignKey(
                        db_column="source_uuid",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="reporting.tenantapiprovider",
                    ),
                ),
            ],
            options={
                "db_table": "reporting_ocp_cost_breakdown_by_project_p",
            },
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownbyprojectp",
            index=models.Index(fields=["usage_start"], name="ocp_brkdwn_proj_usage_start"),
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownbyprojectp",
            index=models.Index(fields=["namespace"], name="ocp_brkdwn_proj_namespace"),
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownbyprojectp",
            index=models.Index(fields=["cost_model_rate_name"], name="ocp_brkdwn_proj_rate_name"),
        ),
        migrations.CreateModel(
            name="OCPCostBreakdownByNodeP",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("cluster_id", models.TextField()),
                ("cluster_alias", models.TextField(null=True)),
                ("node", models.CharField(max_length=253, null=True)),
                ("usage_start", models.DateField()),
                ("usage_end", models.DateField()),
                ("cost_model_cpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_memory_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_volume_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_gpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_rate_type", models.TextField(null=True)),
                ("cost_model_rate_name", models.TextField(null=True)),
                ("infrastructure_raw_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("infrastructure_markup_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("raw_currency", models.TextField(null=True)),
                ("distributed_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                (
                    "cost_category",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.CASCADE, to="reporting.openshiftcostcategory"
                    ),
                ),
                (
                    "source_uuid",
                    models.ForeignKey(
                        db_column="source_uuid",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="reporting.tenantapiprovider",
                    ),
                ),
            ],
            options={
                "db_table": "reporting_ocp_cost_breakdown_by_node_p",
            },
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownbynodep",
            index=models.Index(fields=["usage_start"], name="ocp_brkdwn_node_usage_start"),
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownbynodep",
            index=models.Index(fields=["node"], name="ocp_brkdwn_node_node"),
        ),
        migrations.AddIndex(
            model_name="ocpcostbreakdownbynodep",
            index=models.Index(fields=["cost_model_rate_name"], name="ocp_brkdwn_node_rate_name"),
        ),
        migrations.CreateModel(
            name="OCPVMBreakdownP",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("cluster_id", models.TextField()),
                ("cluster_alias", models.TextField(null=True)),
                ("namespace", models.CharField(max_length=253, null=True)),
                ("node", models.CharField(max_length=253, null=True)),
                ("vm_name", models.TextField(null=True)),
                ("usage_start", models.DateField()),
                ("usage_end", models.DateField()),
                ("cost_model_cpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_memory_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_volume_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_gpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_rate_type", models.TextField(null=True)),
                ("cost_model_rate_name", models.TextField(null=True)),
                ("infrastructure_raw_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("infrastructure_markup_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("raw_currency", models.TextField(null=True)),
                ("distributed_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                (
                    "source_uuid",
                    models.ForeignKey(
                        db_column="source_uuid",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        to="reporting.tenantapiprovider",
                    ),
                ),
            ],
            options={
                "db_table": "reporting_ocp_vm_breakdown_p",
            },
        ),
        migrations.AddIndex(
            model_name="ocpvmbreakdownp",
            index=models.Index(fields=["usage_start"], name="ocp_vm_brkdwn_usage_start"),
        ),
        migrations.AddIndex(
            model_name="ocpvmbreakdownp",
            index=models.Index(fields=["cost_model_rate_name"], name="ocp_vm_brkdwn_rate_name"),
        ),
        migrations.RunPython(code=unset_pg_extended_mode, reverse_code=set_pg_extended_mode),
    ]
