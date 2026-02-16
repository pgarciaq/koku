#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Data migration to populate name field on existing cost model rates."""
from django.db import migrations


def populate_rate_names(apps, schema_editor):
    """Populate name field for existing rates using auto-generation."""
    # Import here to keep the migration self-contained but testable.
    from cost_models.rate_name_utils import generate_name

    CostModel = apps.get_model("cost_models", "CostModel")
    for cost_model in CostModel.objects.all():
        rates = cost_model.rates
        if not rates:
            continue
        modified = False
        used_names = set()
        for rate in rates:
            if rate.get("name"):
                used_names.add(rate["name"])
                continue
            name = generate_name(rate, used_names)
            rate["name"] = name
            used_names.add(name)
            modified = True
        if modified:
            cost_model.rates = rates
            cost_model.save(update_fields=["rates"])


def reverse_rate_names(apps, schema_editor):
    """Remove auto-generated names (reverse migration).

    We cannot distinguish auto-generated from user-provided names,
    so this is a no-op — names are left in place. The serializer
    will continue to accept them.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cost_models", "0009_alter_costmodel_source_type_and_more"),
    ]

    operations = [
        migrations.RunPython(populate_rate_names, reverse_rate_names),
    ]
