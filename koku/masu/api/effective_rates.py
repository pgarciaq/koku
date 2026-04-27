#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""View for effective_rates masu admin endpoint."""
import logging
from decimal import Decimal

from django.db import connection
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
from api.provider.models import Provider
from api.provider.models import ProviderAuthentication
from api.utils import DateHelper
from cost_models.models import CostModel
from cost_models.models import CostModelMap

LOG = logging.getLogger(__name__)


def _resolve_provider_uuid(cluster_id: str) -> str | None:
    """Resolve an OCP cluster_id to a Provider UUID via ProviderAuthentication credentials."""
    for auth in ProviderAuthentication.objects.all():
        if auth.credentials.get("cluster_id") == cluster_id:
            provider = Provider.objects.filter(authentication=auth).first()
            if provider:
                return str(provider.uuid)
    return None


def _parse_rates_list(rates_list: list) -> dict:
    """Parse a CostModel.rates JSON list into a dict keyed by metric name."""
    configured_rates: dict = {}
    for rate in rates_list:
        metric_info = rate.get("metric", {})
        metric_name = metric_info.get("name", "") if isinstance(metric_info, dict) else ""
        if not metric_name:
            continue

        cost_type = (rate.get("cost_type") or "").lower()
        tiered = rate.get("tiered_rates") or []
        value = float(tiered[0].get("value", 0)) if tiered and isinstance(tiered, list) else 0.0

        if metric_name not in configured_rates:
            configured_rates[metric_name] = {"infrastructure": 0.0, "supplementary": 0.0}

        if cost_type in ("infrastructure", "supplementary"):
            configured_rates[metric_name][cost_type] = value

    return configured_rates


def _get_configured_rates(schema_name: str, provider_uuid: str) -> tuple[dict, str, float]:
    """Extract configured cost model rates, distribution type, and markup for a provider.

    Returns (configured_rates_dict, distribution_type, markup_pct).
    """
    configured_rates: dict = {}
    distribution_type = "cpu"
    markup_pct = 0.0

    with schema_context(schema_name):
        cost_model_map = CostModelMap.objects.filter(provider_uuid=provider_uuid).first()
        if not cost_model_map:
            return configured_rates, distribution_type, markup_pct

        cost_model: CostModel = cost_model_map.cost_model
        if not cost_model:
            return configured_rates, distribution_type, markup_pct

        distribution_type = cost_model.distribution or "cpu"

        markup = cost_model.markup or {}
        if isinstance(markup, dict) and markup.get("value") is not None:
            markup_pct = float(markup["value"])

        rates_list = cost_model.rates or []
        if isinstance(rates_list, list):
            configured_rates = _parse_rates_list(rates_list)

    return configured_rates, distribution_type, markup_pct


def _get_namespace_aggregates(schema_name: str, cluster_id: str, start_date: str, end_date: str) -> dict:
    """Query OCPUsageLineItemDailySummary for namespace-level cost/usage aggregates."""
    sql = """
        SELECT namespace,
            SUM(COALESCE(cost_model_cpu_cost, 0))    AS cost_model_cpu_cost,
            SUM(COALESCE(cost_model_memory_cost, 0)) AS cost_model_memory_cost,
            SUM(COALESCE(infrastructure_raw_cost, 0)
                + COALESCE(infrastructure_markup_cost, 0)) AS infrastructure_cost,
            SUM(COALESCE(distributed_cost, 0))       AS distributed_cost,
            SUM(COALESCE(pod_usage_cpu_core_hours, 0)) AS cpu_usage_hours,
            SUM(COALESCE(pod_request_cpu_core_hours, 0)) AS cpu_request_hours,
            SUM(COALESCE(pod_usage_memory_gigabyte_hours, 0)) AS mem_usage_hours,
            SUM(COALESCE(pod_request_memory_gigabyte_hours, 0)) AS mem_request_hours
        FROM reporting_ocpusagelineitem_daily_summary
        WHERE cluster_id = %s
            AND usage_start BETWEEN %s AND %s
            AND data_source = 'Pod'
            AND (cost_model_rate_type IN (
                    'Infrastructure', 'Supplementary',
                    'platform_distributed', 'worker_distributed',
                    'unattributed_storage', 'unattributed_network',
                    'gpu_distributed'
                 ) OR cost_model_rate_type IS NULL)
        GROUP BY namespace
    """
    namespaces = {}
    with schema_context(schema_name):
        with connection.cursor() as cursor:
            cursor.execute(sql, [cluster_id, start_date, end_date])
            columns = [col[0] for col in cursor.description]
            for row in cursor.fetchall():
                row_dict = dict(zip(columns, row))
                ns = row_dict.pop("namespace")
                namespaces[ns] = {k: float(v) if isinstance(v, Decimal) else v for k, v in row_dict.items()}

    return namespaces


@never_cache
@api_view(http_method_names=["GET"])
@permission_classes((AllowAny,))
@renderer_classes(tuple(api_settings.DEFAULT_RENDERER_CLASSES))
def effective_rates(request):
    """Return cost model rates and namespace-level cost/usage aggregates for a cluster.

    Query parameters:
        cluster_id (required): OpenShift cluster ID
        org_id (required): Organization ID (maps to schema org{org_id})
        start_date: Start date YYYY-MM-DD (defaults to this month start)
        end_date: End date YYYY-MM-DD (defaults to today)
    """
    params = request.query_params
    cluster_id = params.get("cluster_id")
    org_id = params.get("org_id")

    if not cluster_id or not org_id:
        return Response(
            {"Error": "cluster_id and org_id are required parameters."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    schema_name = f"org{org_id}"

    try:
        Customer.objects.get(org_id=org_id)
    except Customer.DoesNotExist:
        return Response(
            {"Error": f"Customer with org_id={org_id} does not exist."},
            status=status.HTTP_404_NOT_FOUND,
        )

    provider_uuid = _resolve_provider_uuid(cluster_id)
    if not provider_uuid:
        return Response(
            {"Error": f"No provider found for cluster_id={cluster_id}."},
            status=status.HTTP_404_NOT_FOUND,
        )

    dh = DateHelper()
    start_date = params.get("start_date", dh.this_month_start.strftime("%Y-%m-%d"))
    end_date = params.get("end_date", dh.today.strftime("%Y-%m-%d"))

    configured_rates, distribution_type, markup_pct = _get_configured_rates(schema_name, provider_uuid)
    namespace_aggregates = _get_namespace_aggregates(schema_name, cluster_id, start_date, end_date)

    return Response(
        {
            "cluster_id": cluster_id,
            "provider_uuid": provider_uuid,
            "distribution_type": distribution_type,
            "markup_pct": markup_pct,
            "configured_rates": configured_rates,
            "namespace_aggregates": namespace_aggregates,
        }
    )
