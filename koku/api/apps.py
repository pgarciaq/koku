#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""API application configuration module."""
import logging

from django.apps import AppConfig
from django.db import DatabaseError
from django.db.models import Q

LOG = logging.getLogger(__name__)


def validate_customer_org_ids() -> None:
    """Log errors for Customer records with double-prefixed org_id or schema_name."""
    from api.iam.models import Customer

    bad_customers = Customer.objects.filter(Q(org_id__startswith="org") | Q(schema_name__startswith="orgorg"))
    for customer in bad_customers:
        LOG.error(
            "Customer has invalid org_id/schema_name (org_id=%s, schema_name=%s). "
            "org_id must be the bare number (e.g. 1234567), not prefixed with 'org'.",
            customer.org_id,
            customer.schema_name,
        )


class ApiConfig(AppConfig):
    """API application configuration."""

    name = "api"

    def ready(self):
        """Run startup validation after the app registry is ready."""
        try:
            validate_customer_org_ids()
        except DatabaseError:
            LOG.debug("Skipping customer org_id validation; database not available yet.")
