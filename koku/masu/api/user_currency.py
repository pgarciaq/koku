#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Internal endpoint to look up a tenant's preferred display currency.

SECURITY: This endpoint uses AllowAny and is intentionally internal-only.
Access must be restricted by Kubernetes NetworkPolicy in production.
"""
import logging

from django.views.decorators.cache import never_cache
from django_tenants.utils import schema_context
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.decorators import renderer_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings

from api.iam.models import Customer
from koku.settings import KOKU_DEFAULT_CURRENCY
from reporting.user_settings.models import UserSettings

LOG = logging.getLogger(__name__)


@never_cache
@api_view(http_method_names=["GET"])
@permission_classes((AllowAny,))
@renderer_classes(tuple(api_settings.DEFAULT_RENDERER_CLASSES))
def user_currency(request):
    """Return the preferred display currency for an org_id.

    Query parameters:
        org_id (required): Organization ID (maps to schema org{org_id})

    Response:
        {"currency": "EUR"}

    Falls back to KOKU_DEFAULT_CURRENCY ("USD") when the tenant has no
    UserSettings row or no currency preference set.
    """
    org_id = request.query_params.get("org_id")
    if not org_id:
        return Response(
            {"Error": "org_id is a required parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        Customer.objects.get(org_id=org_id)
    except Customer.DoesNotExist:
        return Response(
            {"Error": f"Customer with org_id={org_id} does not exist."},
            status=status.HTTP_404_NOT_FOUND,
        )

    schema_name = f"org{org_id}"
    currency = KOKU_DEFAULT_CURRENCY

    with schema_context(schema_name):
        settings_row = UserSettings.objects.first()
        if settings_row and settings_row.settings:
            currency = settings_row.settings.get("currency", KOKU_DEFAULT_CURRENCY)

    return Response({"currency": currency})
