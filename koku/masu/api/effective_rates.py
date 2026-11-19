#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""View for effective_rates masu admin endpoint.

SECURITY: This endpoint uses AllowAny and is intentionally internal-only.
In production on-prem deployments, access must be restricted by Kubernetes
NetworkPolicy (cost-onprem masu-access policy) to ros-processor and Koku workers.

RATE SOURCE (#467): rates are resolved through
`CostModelDBAccessor(..., price_list_effective_on=start_date)`, so ROS sees the
same list Masu applied for that window — including the `disable_price_list`
Unleash kill-switch and the "no price list covers the date => no rates" rule.

MONEY SCALE (#461):

* `rates[]` is always the cost model's rows as-is (`tiered_rates` **or**
  `tag_rates`), with money in **integer micro-cents** (`1 dollar =
  100_000_000`), per the contract both #467 and #461 signed off on.
* `configured_rates`, `namespace_aggregates` and `markup_pct` follow the
  requested `money_scale`. `money_scale=microcents` emits integer micro-cents
  (money), integer milli-hours (`cpu_*_hours` / `mem_*_hours` keep their
  names) and integer basis points (`markup_pct`), and the response echoes
  `"money_scale": "microcents"`. Without the parameter those fields keep the
  legacy **float dollars** scale and the response echoes `"money_scale":
  "dollars"`.
* The float scale exists only for a reader that predates #461. ROS sends
  `money_scale=microcents` and requires the echo, so a stale Masu is a named
  error there rather than a silent 100_000_000x inflation of applied savings.

`configured_rates` is compatibility only: it flattens **tiered** rows and omits
metrics priced by tags, rather than asserting a `$0` the tags contradict.
"""
import logging
from datetime import date
from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_HALF_UP

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
from masu.database.cost_model_db_accessor import CostModelDBAccessor

LOG = logging.getLogger(__name__)

# 1 dollar = 100_000_000 micro-cents (the ROS int64 money scale, #461).
MICRO_CENTS_PER_DOLLAR = 100_000_000
MILLI_HOURS_PER_HOUR = 1000
BASIS_POINTS_PER_PERCENT = 100

# `money_scale` values. `dollars` is the legacy float scale and the default;
# `microcents` is the #461 contract (integers all the way down).
MONEY_SCALE_DOLLARS = "dollars"
MONEY_SCALE_MICROCENTS = "microcents"
MONEY_SCALES = (MONEY_SCALE_DOLLARS, MONEY_SCALE_MICROCENTS)

# `namespace_aggregates` columns by scale. A column added to the SQL below that
# is in neither set is reported rather than silently left as a float.
MONEY_AGGREGATE_FIELDS = frozenset(
    {
        "cost_model_cpu_cost",
        "cost_model_memory_cost",
        "cost_model_gpu_cost",
        "infrastructure_cost",
        "distributed_cost",
    }
)
HOUR_AGGREGATE_FIELDS = frozenset(
    {
        "cpu_usage_hours",
        "cpu_request_hours",
        "mem_usage_hours",
        "mem_request_hours",
    }
)


def _resolve_provider_uuid(cluster_id: str, org_id: str) -> str | None:
    """Resolve an OCP cluster_id to the REQUESTING CUSTOMER's Provider UUID.

    #771 (V15-21): koku explicitly supports the same cluster_id across
    customers (shared authentication objects), so the resolution must be
    customer-qualified — a global scan returning the first matching provider
    hands the requesting tenant a foreign provider, and the tenant's
    cost-model lookup then receives the wrong provider ID (which ROS reship
    trusts).
    """
    for auth in ProviderAuthentication.objects.all():
        if auth.credentials.get("cluster_id") == cluster_id:
            provider = Provider.objects.filter(authentication=auth, customer__org_id=org_id).first()
            if provider:
                return str(provider.uuid)
    return None


def _metric_name(rate: dict) -> str:
    """Return the metric name of a stored rate row, or '' when absent."""
    if not isinstance(rate, dict):
        return ""
    metric_info = rate.get("metric") or {}
    if not isinstance(metric_info, dict):
        return ""
    return metric_info.get("name") or ""


def _to_micro_cents(value) -> int:
    """Convert a dollar amount (str/Decimal/int/float) to integer micro-cents."""
    if value is None:
        return 0
    try:
        return int((Decimal(str(value)) * MICRO_CENTS_PER_DOLLAR).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        # A stored row we cannot price must be loud, not silently $0.
        LOG.warning(f"effective_rates: unparseable rate value {value!r}")
        return 0


def _to_milli_hours(value) -> int:
    """Convert an hours amount (str/Decimal/int/float) to integer milli-hours."""
    if value is None:
        return 0
    try:
        return int((Decimal(str(value)) * MILLI_HOURS_PER_HOUR).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        LOG.warning(f"effective_rates: unparseable hours value {value!r}")
        return 0


def _to_basis_points(value) -> int:
    """Convert a percentage (10 means 10%) to integer basis points (1000)."""
    if value is None:
        return 0
    try:
        return int((Decimal(str(value)) * BASIS_POINTS_PER_PERCENT).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        LOG.warning(f"effective_rates: unparseable markup value {value!r}")
        return 0


def _serialize_tier(tier: dict) -> dict:
    """One tiered rate in the shape `RateSerializer` emits, money in micro-cents."""
    out = {"unit": tier.get("unit"), "value": _to_micro_cents(tier.get("value"))}
    usage = tier.get("usage")
    if not usage and (tier.get("usage_start") is not None or tier.get("usage_end") is not None):
        # RateSerializer._normalize_tiered_rate folds the bare bounds into `usage`.
        usage = {
            "usage_start": tier.get("usage_start"),
            "usage_end": tier.get("usage_end"),
            "unit": tier.get("unit"),
        }
    if usage:
        out["usage"] = usage
    return out


def _serialize_rate_row(rate: dict) -> dict:
    """Mirror `RateSerializer.to_representation` for one stored rate row.

    Same keys the public cost-models API emits (`metric`, `description`,
    `custom_name`, `tiered_rates` **or** `tag_rates`, `cost_type`), minus the
    internal `rate_id`, with money converted to integer micro-cents.
    """
    row = {
        "metric": {"name": _metric_name(rate)},
        "description": rate.get("description", ""),
    }
    if "custom_name" in rate:
        row["custom_name"] = rate["custom_name"]

    tiered_rates = rate.get("tiered_rates") or []
    if tiered_rates:
        row["tiered_rates"] = [_serialize_tier(tier) for tier in tiered_rates if isinstance(tier, dict)]
        row["cost_type"] = rate.get("cost_type")
        return row

    tag_rates = rate.get("tag_rates") or {}
    if tag_rates:
        row["tag_rates"] = {
            "tag_key": tag_rates.get("tag_key"),
            "tag_values": [
                {**tag_value, "value": _to_micro_cents(tag_value.get("value"))}
                for tag_value in tag_rates.get("tag_values") or []
                if isinstance(tag_value, dict)
            ],
        }
        row["cost_type"] = rate.get("cost_type")
    return row


def _has_prices(rate: dict) -> bool:
    """A stored row priced by tiers or by tags — anything else carries no rate."""
    return bool((rate.get("tiered_rates") or []) or (rate.get("tag_rates") or {}))


def _parse_rates_list(rates_list: list, microcents: bool = False) -> dict:
    """Flatten **tiered** rows to {metric: {infrastructure, supplementary}}.

    Money is integer micro-cents under `microcents` (#461) and float dollars
    otherwise — the legacy scale, kept for a reader that predates #461.

    Compatibility for the deployed ROS reader (#467 contract point 2). Metrics
    priced only by tags are omitted rather than reported as `0`: ROS resolves a
    missing key and a `0` identically, so this is not a reader-visible
    regression, and it stops the endpoint asserting "no rate" where tags hold one.
    """
    configured_rates: dict = {}
    for rate in rates_list:
        metric_name = _metric_name(rate)
        if not metric_name:
            continue

        tiered = rate.get("tiered_rates") or []
        first_tier = tiered[0] if isinstance(tiered, list) and tiered else None
        if not isinstance(first_tier, dict):
            continue

        cost_type = (rate.get("cost_type") or "").lower()
        if cost_type not in ("infrastructure", "supplementary"):
            continue

        raw_value = first_tier.get("value", 0)
        try:
            parsed_value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError, TypeError):
            LOG.warning(f"effective_rates: unparseable tiered value for {metric_name}: {raw_value!r}")
            continue

        if microcents:
            value = int((parsed_value * MICRO_CENTS_PER_DOLLAR).to_integral_value(rounding=ROUND_HALF_UP))
            empty = 0
        else:
            value = float(parsed_value)
            empty = 0.0

        configured_rates.setdefault(metric_name, {"infrastructure": empty, "supplementary": empty})
        configured_rates[metric_name][cost_type] = value

    return configured_rates


def _extract_currency(rates_list: list) -> str:
    """Return the currency unit from tiered rates, else from tag values, else USD."""
    for rate in rates_list:
        for tier in rate.get("tiered_rates") or []:
            if isinstance(tier, dict) and tier.get("unit"):
                return str(tier["unit"])
    for rate in rates_list:
        tag_rates = rate.get("tag_rates") or {}
        for tag_value in tag_rates.get("tag_values") or []:
            if isinstance(tag_value, dict) and tag_value.get("unit"):
                return str(tag_value["unit"])
    return "USD"


def _get_cost_snapshot(
    schema_name: str, provider_uuid: str, effective_on: date, microcents: bool = False
) -> tuple[list, dict, str, int | float, str | None]:
    """Resolve the provider's cost model for the requested window.

    Reads through `CostModelDBAccessor` so the rates are the ones Masu applied:
    price-list resolution for `effective_on` and the `disable_price_list`
    kill-switch both live there (#467 contract point 4).

    Returns (rates, configured_rates, distribution_type, markup_pct, currency).
    `markup_pct` is integer basis points under `microcents` and float percent
    otherwise. `currency` is None when the provider has no cost model at all —
    do not invent a currency for a model that does not exist.
    """
    with CostModelDBAccessor(schema_name, provider_uuid, price_list_effective_on=effective_on) as accessor:
        cost_model = accessor.cost_model
        if not cost_model:
            return [], {}, "cpu", 0 if microcents else 0.0, None

        distribution_type = cost_model.distribution or "cpu"

        markup = accessor.markup or {}
        markup_pct: int | float = 0 if microcents else 0.0
        if isinstance(markup, dict) and markup.get("value") is not None:
            if microcents:
                markup_pct = _to_basis_points(markup["value"])
            else:
                markup_pct = float(markup["value"])

        effective_rates = accessor.effective_rates
        if isinstance(effective_rates, dict):
            # PriceList.rates / CostModel.rates default to {} when unset.
            effective_rates = []
        rate_rows = [row for row in effective_rates if _metric_name(row) and _has_prices(row)]

        return (
            [_serialize_rate_row(row) for row in rate_rows],
            _parse_rates_list(rate_rows, microcents=microcents),
            distribution_type,
            markup_pct,
            _extract_currency(rate_rows),
        )


def _get_namespace_aggregates(
    schema_name: str, cluster_id: str, start_date: str, end_date: str, microcents: bool = False
) -> dict:
    """Query OCPUsageLineItemDailySummary for namespace-level cost/usage aggregates.

    Under `microcents` money columns are integer micro-cents and hour columns are
    integer milli-hours (#461); otherwise every value stays the legacy float
    dollars / float hours.
    """
    sql = """
        SELECT namespace,
            SUM(COALESCE(cost_model_cpu_cost, 0))    AS cost_model_cpu_cost,
            SUM(COALESCE(cost_model_memory_cost, 0)) AS cost_model_memory_cost,
            SUM(COALESCE(cost_model_gpu_cost, 0))    AS cost_model_gpu_cost,
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
            AND data_source IN ('Pod', 'GPU')
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
                if microcents:
                    converted = {}
                    for field_name, value in row_dict.items():
                        if field_name in MONEY_AGGREGATE_FIELDS:
                            converted[field_name] = _to_micro_cents(value)
                        elif field_name in HOUR_AGGREGATE_FIELDS:
                            converted[field_name] = _to_milli_hours(value)
                        else:
                            # A column this contract does not classify would reach
                            # the wire as an untyped float; name it instead.
                            LOG.warning(f"effective_rates: unclassified aggregate field {field_name!r}")
                            converted[field_name] = value
                    namespaces[ns] = converted
                else:
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
        money_scale: `microcents` for the #461 integer contract, `dollars` (the
            default) for the legacy float scale. Echoed back in the response.
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

    provider_uuid = _resolve_provider_uuid(cluster_id, org_id)
    if not provider_uuid:
        return Response(
            {"Error": f"No provider found for cluster_id={cluster_id}."},
            status=status.HTTP_404_NOT_FOUND,
        )

    dh = DateHelper()
    start_date = params.get("start_date", dh.this_month_start.strftime("%Y-%m-%d"))
    end_date = params.get("end_date", dh.today.strftime("%Y-%m-%d"))

    # Validate date format (YYYY-MM-DD) and logical ordering.
    try:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)
    except (ValueError, TypeError):
        return Response(
            {"Error": "start_date and end_date must be valid YYYY-MM-DD strings."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if parsed_start > parsed_end:
        return Response(
            {"Error": "start_date must not be after end_date."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    money_scale = params.get("money_scale", MONEY_SCALE_DOLLARS)
    if money_scale not in MONEY_SCALES:
        return Response(
            {"Error": f"money_scale must be one of: {', '.join(MONEY_SCALES)}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    microcents = money_scale == MONEY_SCALE_MICROCENTS

    rates, configured_rates, distribution_type, markup_pct, currency = _get_cost_snapshot(
        schema_name, provider_uuid, parsed_start, microcents=microcents
    )
    namespace_aggregates = _get_namespace_aggregates(
        schema_name, cluster_id, start_date, end_date, microcents=microcents
    )

    return Response(
        {
            "cluster_id": cluster_id,
            "provider_uuid": provider_uuid,
            "distribution_type": distribution_type,
            "money_scale": money_scale,
            "markup_pct": markup_pct,
            "currency": currency,
            "rates": rates,
            "configured_rates": configured_rates,
            "namespace_aggregates": namespace_aggregates,
        }
    )
