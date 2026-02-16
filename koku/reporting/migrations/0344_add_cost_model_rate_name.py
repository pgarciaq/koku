#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):

    dependencies = [
        ("reporting", "0343_ocp_line_item_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocpusagelineitemdailysummary",
            name="cost_model_rate_name",
            field=models.TextField(null=True),
        ),
        migrations.AddIndex(
            model_name="ocpusagelineitemdailysummary",
            index=models.Index(fields=["cost_model_rate_name"], name="cost_model_rate_name_idx"),
        ),
    ]
