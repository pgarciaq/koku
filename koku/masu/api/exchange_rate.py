#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Internal endpoint to look up an exchange rate for a currency pair.

SECURITY: This endpoint uses AllowAny and is intentionally internal-only.
Access must be restricted by Kubernetes NetworkPolicy in production.
"""
import datetime
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

from api.currency.models import ExchangeRateDictionary
from api.iam.models import Tenant
from cost_models.models import MonthlyExchangeRate
from masu.processor import CONSTANT_CURRENCY_FLAG
from masu.processor import is_feature_flag_enabled_by_schema

LOG = logging.getLogger(__name__)


def _get_rate_from_monthly(schema, from_currency, to_currency):
    """Try to resolve a rate from MonthlyExchangeRate for the current month.

    Returns a Decimal rate or None.
    """
    today = datetime.date.today()
    first_of_month = today.replace(day=1)
    with schema_context(schema):
        row = (
            MonthlyExchangeRate.objects.filter(
                base_currency=from_currency,
                target_currency=to_currency,
                effective_date=first_of_month,
            )
            .values_list("exchange_rate", flat=True)
            .first()
        )
    return row


def _get_rate_from_dictionary(from_currency, to_currency):
    """Look up a rate from the global ExchangeRateDictionary (public schema).

    Returns a Decimal/float rate or None.
    """
    erd = ExchangeRateDictionary.objects.first()
    if not erd or not erd.currency_exchange_dictionary:
        return None
    from_rates = erd.currency_exchange_dictionary.get(from_currency)
    if not from_rates:
        return None
    return from_rates.get(to_currency)


@never_cache
@api_view(http_method_names=["GET"])
@permission_classes((AllowAny,))
@renderer_classes(tuple(api_settings.DEFAULT_RENDERER_CLASSES))
def exchange_rate(request):
    """Return the exchange rate for a currency pair.

    Query parameters:
        schema (required): Tenant schema name (e.g. org1234567)
        from   (required): Source currency code (e.g. EUR)
        to     (required): Target currency code (e.g. GBP)

    Response:
        {"from_currency": "EUR", "to_currency": "GBP", "rate": "0.860000000000000"}

    Resolution order:
        1. If constant-currency flag is enabled for the schema, query
           MonthlyExchangeRate for the current month.
        2. Otherwise fall back to ExchangeRateDictionary (global daily matrix).
        3. If neither has the pair, rate is null.

    The rate is returned as a JSON string to preserve full Decimal precision.
    """
    params = request.query_params
    schema = params.get("schema")
    from_currency = (params.get("from") or "").upper()
    to_currency = (params.get("to") or "").upper()

    if not schema or not from_currency or not to_currency:
        return Response(
            {"Error": "schema, from, and to are required parameters."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not Tenant.objects.filter(schema_name=schema).exists():
        return Response(
            {"Error": f"Schema '{schema}' does not exist."},
            status=status.HTTP_404_NOT_FOUND,
        )

    if from_currency == to_currency:
        return Response({"from_currency": from_currency, "to_currency": to_currency, "rate": "1"})

    rate = None

    if is_feature_flag_enabled_by_schema(schema, CONSTANT_CURRENCY_FLAG):
        rate = _get_rate_from_monthly(schema, from_currency, to_currency)

    if rate is None:
        rate = _get_rate_from_dictionary(from_currency, to_currency)

    rate_str = str(rate) if rate is not None else None

    return Response({"from_currency": from_currency, "to_currency": to_currency, "rate": rate_str})
