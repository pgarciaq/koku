#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""OCP Query Handling for Reports."""
import copy
import logging
from collections import defaultdict
from decimal import Decimal
from decimal import DivisionByZero
from decimal import InvalidOperation
from functools import cached_property

from django.db.models import Case
from django.db.models import CharField
from django.db.models import DecimalField
from django.db.models import F
from django.db.models import Max
from django.db.models import Sum
from django.db.models import Value
from django.db.models import When
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce
from django_tenants.utils import tenant_context

from api.models import Provider
from api.report.ocp.capacity.cluster_capacity import calculate_unused
from api.report.ocp.capacity.cluster_capacity import ClusterCapacity
from api.report.ocp.capacity.node_capacity import NodeCapacity
from api.report.ocp.provider_map import OCPProviderMap
from api.report.queries import is_grouped_by_node
from api.report.queries import is_grouped_by_project
from api.report.queries import ReportQueryHandler
from cost_models.models import CostModel
from cost_models.models import CostModelMap

LOG = logging.getLogger(__name__)


class OCPReportQueryHandler(ReportQueryHandler):
    """Handles report queries and responses for OCP."""

    provider = Provider.PROVIDER_OCP

    def __init__(self, parameters):
        """Establish OCP report query handler.

        Args:
            parameters    (QueryParameters): parameter object for query

        """
        mapper_class = OCPProviderMap
        self._limit = parameters.get_filter("limit")
        self._report_type = parameters.report_type
        # Update which field is used to calculate cost by group by param.
        if is_grouped_by_project(parameters) and parameters.report_type == "costs":
            self._report_type = parameters.report_type + "_by_project"
        self._mapper = mapper_class(
            provider=self.provider, report_type=self._report_type, schema_name=parameters.tenant.schema_name
        )
        self.group_by_options = self._mapper.report_type_map.get("group_by_options") or self._mapper.provider_map.get(
            "group_by_options"
        )

        # We need to overwrite the default pack definitions with these
        # Order of the keys matters in how we see it in the views.
        ocp_pack_keys = {
            "infra_raw": {"key": "raw", "group": "infrastructure"},
            "infra_markup": {"key": "markup", "group": "infrastructure"},
            "infra_usage": {"key": "usage", "group": "infrastructure"},
            "infra_distributed": {"key": "distributed", "group": "infrastructure"},
            "infra_total": {"key": "total", "group": "infrastructure"},
            "sup_raw": {"key": "raw", "group": "supplementary"},
            "sup_markup": {"key": "markup", "group": "supplementary"},
            "sup_usage": {"key": "usage", "group": "supplementary"},
            "sup_distributed": {"key": "distributed", "group": "supplementary"},
            "sup_total": {"key": "total", "group": "supplementary"},
            "cost_raw": {"key": "raw", "group": "cost"},
            "cost_markup": {"key": "markup", "group": "cost"},
            "cost_usage": {"key": "usage", "group": "cost"},
            "cost_platform_distributed": {"key": "platform_distributed", "group": "cost"},
            "cost_worker_unallocated_distributed": {"key": "worker_unallocated_distributed", "group": "cost"},
            "cost_network_unattributed_distributed": {"key": "network_unattributed_distributed", "group": "cost"},
            "cost_storage_unattributed_distributed": {"key": "storage_unattributed_distributed", "group": "cost"},
            "cost_gpu_unallocated_distributed": {"key": "gpu_unallocated_distributed", "group": "cost"},
            "cost_total_distributed": {"key": "distributed", "group": "cost"},
            "cost_total": {"key": "total", "group": "cost"},
        }
        ocp_pack_definitions = copy.deepcopy(self._mapper.PACK_DEFINITIONS)
        ocp_pack_definitions["cost_groups"]["keys"] = ocp_pack_keys
        ocp_pack_definitions["request_cpu"] = {"keys": ["request_cpu"], "units": "request_cpu_units"}
        ocp_pack_definitions["request_memory"] = {"keys": ["request_memory"], "units": "request_memory_units"}
        ocp_pack_definitions["request"] = {
            "keys": {
                "request_cpu": {"key": "cpu", "group": "request"},
                "request_memory": {"key": "memory", "group": "request"},
            },
            "units": "usage_units",
        }
        # Note: The value & units will be supplied by the usage keys in the parent class.
        ocp_pack_definitions["unused_usage"] = {
            "keys": {
                "request_unused": {"key": "unused", "group": "request"},
                "request_unused_percent": {"key": "unused_percent", "group": "request"},
                "capacity_unused": {"key": "unused", "group": "capacity"},
                "capacity_unused_percent": {"key": "unused_percent", "group": "capacity"},
                "capacity_count": {"key": "count", "group": "capacity"},
                "capacity_count_units": {"key": "count_units", "group": "capacity"},
            },
            "units": "usage_units",
        }
        ocp_pack_definitions["usage"]["keys"].extend(["data_transfer_in", "data_transfer_out"])
        ocp_pack_definitions["gpu_memory"] = {"keys": ["gpu_memory"], "units": "gpu_memory_units"}
        ocp_pack_definitions["gpu_count"] = {"keys": ["gpu_count"], "units": "gpu_count_units"}

        # super() needs to be called after _mapper and _limit is set
        super().__init__(parameters)
        # super() needs to be called before _get_group_by is called

        self._mapper.PACK_DEFINITIONS = ocp_pack_definitions

    @property
    def annotations(self):
        """Create dictionary for query annotations.

        Returns:
            (Dict): query annotations dictionary

        """
        annotations = {
            "date": self.date_trunc("usage_start"),
            # this currency is used by the provider map to populate the correct currency value
            "currency_annotation": Value(self.currency, output_field=CharField()),
            **self.exchange_rate_annotation_dict,
        }
        # { query_param: database_field_name }
        fields = self._mapper.provider_map.get("annotations")
        for q_param, db_field in fields.items():
            annotations[q_param] = F(db_field)
        if is_grouped_by_project(self.parameters):
            if self._category:
                annotations["project"] = Coalesce(F("cost_category__name"), F("namespace"), output_field=CharField())
            else:
                annotations["project"] = F("namespace")

        if is_grouped_by_node(self.parameters):
            # This adds the instance counts to the node group by.
            if self._mapper.report_type_map.get("capacity_aggregate", {}).get("node"):
                self.report_annotations.update(
                    self._mapper.report_type_map.get("capacity_aggregate", {}).get("node", {})
                )
        for tag_db_name, _, original_tag in self._tag_group_by:
            annotations[tag_db_name] = KT(f"{self._mapper.tag_column}__{original_tag}")

        if special_annotations := self._mapper.report_type_map.get("report_type_annotations"):
            annotations.update(special_annotations)

        return annotations

    @cached_property
    def source_to_currency_map(self):
        """
        OCP sources do not have costs associated, so we need to
        grab the base currency from the cost model, and create
        a mapping of source_uuid to currency.
        returns:
            dict: {source_uuid: currency}
        """
        source_map = defaultdict(lambda: self._mapper.cost_units_fallback)
        cost_models = CostModel.objects.all().values("uuid", "currency").distinct()
        cm_to_currency = {row["uuid"]: row["currency"] for row in cost_models}
        mapping = CostModelMap.objects.all().values("provider_uuid", "cost_model_id")
        source_map |= {row["provider_uuid"]: cm_to_currency[row["cost_model_id"]] for row in mapping}
        return source_map

    @cached_property
    def exchange_rate_annotation_dict(self):
        """Get the exchange rate annotation based on the exchange_rates property."""
        exchange_rate_whens = [
            When(**{"source_uuid": uuid, "then": Value(self.exchange_rates.get(cur, {}).get(self.currency, 1))})
            for uuid, cur in self.source_to_currency_map.items()
        ]
        infra_exchange_rate_whens = [
            When(**{self._mapper.cost_units_key: k, "then": Value(v.get(self.currency))})
            for k, v in self.exchange_rates.items()
        ]
        return {
            "exchange_rate": Case(*exchange_rate_whens, default=1, output_field=DecimalField()),
            "infra_exchange_rate": Case(*infra_exchange_rate_whens, default=1, output_field=DecimalField()),
        }

    def format_tags(self, tags_iterable):
        """
        Formats the tags into our standard format.
        """
        if not tags_iterable:
            return []
        transformed_tags = defaultdict(lambda: {"values": set()})

        for tag in tags_iterable:
            if tag:
                for key, value in tag.items():
                    transformed_tags[key]["values"].add(value)

        return [{"key": key, "values": list(data["values"])} for key, data in transformed_tags.items()]

    def _format_query_response(self):
        """Format the query response with data.

        Returns:
            (Dict): Dictionary response of query params, data, and total

        """

        output = self._initialize_response_output(self.parameters)
        if self._report_type == "costs_by_project":
            # Add a boolean flag for the overhead dropdown in the UI
            with tenant_context(self.tenant):
                output["distributed_overhead"] = False
                if (
                    self.query_table.objects.filter(self.query_filter)
                    .filter(cost_model_rate_type__in=["platform_distributed", "worker_distributed"])
                    .exists()
                ):
                    output["distributed_overhead"] = True

        output["data"] = self.query_data
        self.query_sum = self._pack_data_object(self.query_sum, **self._mapper.PACK_DEFINITIONS)
        output["total"] = self.query_sum

        if self._delta:
            output["delta"] = self.query_delta

        breakdown_table = self._breakdown_table
        if breakdown_table and not self.is_csv_output:
            breakdown_limit = self.parameters.get("breakdown_limit")
            with tenant_context(self.tenant):
                self._attach_total_breakdown(output, breakdown_table, breakdown_limit)
                self._attach_data_row_breakdown(output, breakdown_table, breakdown_limit)

        return output

    # --- Cost Breakdown Methods ---

    @cached_property
    def _breakdown_table(self):
        """Select the breakdown table based on report type and group-by."""
        breakdown_views = getattr(self._mapper, "breakdown_views", {})
        if not breakdown_views:
            return None

        report_group = "default"
        key_tuple = tuple(
            sorted(
                self.query_table_filter_keys.union(
                    self.query_table_group_by_keys, self.query_table_access_keys, self.query_table_exclude_keys
                )
            )
        )
        if key_tuple:
            report_group = key_tuple

        try:
            return breakdown_views[self._report_type][report_group]
        except KeyError:
            return breakdown_views.get(self._report_type, {}).get("default")

    def _query_breakdown_aggregate(self, breakdown_table, extra_group_fields=None):
        """Query the breakdown table and aggregate by rate type and name.

        Returns a QuerySet of dicts with total_cost, total_distributed, etc.
        """
        breakdown_qs = breakdown_table.objects.filter(self.query_filter)
        if self.query_exclusions:
            breakdown_qs = breakdown_qs.exclude(self.query_exclusions)

        group_fields = ["cost_model_rate_type", "cost_model_rate_name"]
        if extra_group_fields:
            group_fields = list(extra_group_fields) + group_fields

        _decimal = DecimalField(max_digits=33, decimal_places=15)
        return breakdown_qs.values(*group_fields).annotate(
            total_cost=Sum(
                Coalesce(F("cost_model_cpu_cost"), Value(0, output_field=_decimal))
                + Coalesce(F("cost_model_memory_cost"), Value(0, output_field=_decimal))
                + Coalesce(F("cost_model_volume_cost"), Value(0, output_field=_decimal))
                + Coalesce(F("cost_model_gpu_cost"), Value(0, output_field=_decimal)),
                output_field=_decimal,
            ),
            total_distributed=Sum(
                Coalesce(F("distributed_cost"), Value(0, output_field=_decimal)), output_field=_decimal
            ),
            total_raw_cost=Sum(
                Coalesce(F("infrastructure_raw_cost"), Value(0, output_field=_decimal)), output_field=_decimal
            ),
            total_markup_cost=Sum(
                Coalesce(F("infrastructure_markup_cost"), Value(0, output_field=_decimal)), output_field=_decimal
            ),
            currency=Max("raw_currency"),
        )

    def _attach_total_breakdown(self, output, breakdown_table, breakdown_limit):
        """Attach breakdown arrays to the total cost structure."""
        total = output.get("total", {})
        cost = total.get("cost", {})
        if not cost:
            return

        breakdown_entries = list(self._query_breakdown_aggregate(breakdown_table))
        self._inject_breakdown_into_cost(cost, breakdown_entries, breakdown_limit)

    def _attach_data_row_breakdown(self, output, breakdown_table, breakdown_limit):
        """Attach breakdown arrays to each data row using a single prefetch query."""
        data = output.get("data", [])
        if not data:
            return

        group_by_value = self._get_group_by()
        group_by_field = self._resolve_breakdown_group_field(group_by_value)
        extra_group = ["date"]
        if group_by_field:
            extra_group.append(group_by_field)

        breakdown_qs = breakdown_table.objects.filter(self.query_filter)
        if self.query_exclusions:
            breakdown_qs = breakdown_qs.exclude(self.query_exclusions)

        qs_fields = ["cost_model_rate_type", "cost_model_rate_name"]
        annotate_fields = {
            "date": self.date_trunc("usage_start"),
        }
        _decimal = DecimalField(max_digits=33, decimal_places=15)
        raw_data = (
            breakdown_qs.annotate(**annotate_fields)
            .values(*extra_group, *qs_fields)
            .annotate(
                total_cost=Sum(
                    Coalesce(F("cost_model_cpu_cost"), Value(0, output_field=_decimal))
                    + Coalesce(F("cost_model_memory_cost"), Value(0, output_field=_decimal))
                    + Coalesce(F("cost_model_volume_cost"), Value(0, output_field=_decimal))
                    + Coalesce(F("cost_model_gpu_cost"), Value(0, output_field=_decimal)),
                    output_field=_decimal,
                ),
                total_distributed=Sum(
                    Coalesce(F("distributed_cost"), Value(0, output_field=_decimal)),
                    output_field=_decimal,
                ),
                currency=Max("raw_currency"),
            )
        )

        breakdown_index = defaultdict(list)
        for entry in raw_data:
            date_key = str(entry["date"])
            group_key = str(entry.get(group_by_field, "__all__")) if group_by_field else "__all__"
            breakdown_index[(date_key, group_key)].append(entry)

        api_group_key = self._get_api_group_key(group_by_value)
        self._walk_data_rows(data, breakdown_index, api_group_key, group_by_field, breakdown_limit)

    def _resolve_breakdown_group_field(self, group_by_value):
        """Map the first group-by value to the breakdown table column name."""
        if not group_by_value:
            return None
        api_to_db = {
            "project": "namespace",
            "node": "node",
            "cluster": "cluster_id",
            "vm_name": "vm_name",
        }
        for gb in group_by_value:
            if gb in api_to_db:
                return api_to_db[gb]
            if gb.startswith("tag:"):
                return None
        return None

    def _get_api_group_key(self, group_by_value):
        """Get the API-level group-by key used in the transformed data."""
        if not group_by_value:
            return None
        return group_by_value[0] if group_by_value else None

    def _walk_data_rows(self, data, breakdown_index, api_group_key, db_group_field, breakdown_limit):
        """Walk the nested data structure and inject breakdown into each row's cost."""
        for date_entry in data:
            date_str = str(date_entry.get("date", ""))
            if api_group_key:
                plural_key = api_group_key + "s"
                for row in date_entry.get(plural_key, []):
                    group_value = str(row.get(api_group_key, "__all__"))
                    row_entries = breakdown_index.get((date_str, group_value), [])
                    if row_entries:
                        self._inject_into_cost_if_present(row, row_entries, breakdown_limit)
            else:
                row_entries = breakdown_index.get((date_str, "__all__"), [])
                if row_entries:
                    self._inject_into_date_entry(date_entry, row_entries, breakdown_limit)

    def _inject_into_date_entry(self, date_entry, row_entries, breakdown_limit):
        """Inject breakdown into a date entry, handling both nested and flat structures."""
        # After _transform_data, no-group-by data nests cost under "values" list
        for val in date_entry.get("values", []):
            self._inject_into_cost_if_present(val, row_entries, breakdown_limit)
        if not date_entry.get("values"):
            self._inject_into_cost_if_present(date_entry, row_entries, breakdown_limit)

    def _inject_into_cost_if_present(self, row, row_entries, breakdown_limit):
        """Inject breakdown into a row's cost dict if it exists."""
        cost = row.get("cost", {})
        if cost:
            self._inject_breakdown_into_cost(cost, row_entries, breakdown_limit)

    def _inject_breakdown_into_cost(self, cost, breakdown_entries, breakdown_limit):
        """Inject breakdown arrays into a cost structure (total or per-row)."""
        usage = cost.get("usage")
        if usage and isinstance(usage, dict):
            usage_entries = [
                {
                    "name": e["cost_model_rate_name"],
                    "source": "rate",
                    "value": e["total_cost"],
                    "units": e.get("currency") or "USD",
                }
                for e in breakdown_entries
                if e.get("cost_model_rate_type") in ("Infrastructure", "Supplementary")
                and e.get("cost_model_rate_name")
            ]
            if usage_entries:
                usage_entries.sort(key=lambda x: x.get("value") or 0, reverse=True)
                usage["breakdown"] = self._apply_breakdown_limit(usage_entries, breakdown_limit)

        overhead_map = {
            "platform_distributed": "platform_distributed",
            "worker_unallocated_distributed": "worker_distributed",
            "storage_unattributed_distributed": "unattributed_storage",
            "network_unattributed_distributed": "unattributed_network",
            "gpu_unallocated_distributed": "gpu_distributed",
        }
        for cost_key, rate_type in overhead_map.items():
            cost_obj = cost.get(cost_key)
            if cost_obj and isinstance(cost_obj, dict):
                type_entries = [e for e in breakdown_entries if e.get("cost_model_rate_type") == rate_type]
                if type_entries:
                    oh_breakdown = self._build_overhead_breakdown(type_entries)
                    if oh_breakdown:
                        cost_obj["breakdown"] = self._apply_breakdown_limit(oh_breakdown, breakdown_limit)

    def _build_overhead_breakdown(self, entries):
        """Build breakdown for an overhead type from pre-computed distribution data."""
        breakdown = []
        cloud_total = Decimal("0")
        currency = "USD"
        for e in entries:
            name = e.get("cost_model_rate_name")
            currency = e.get("currency") or "USD"
            dist = e.get("total_distributed") or Decimal("0")
            if name:
                breakdown.append({"name": name, "source": "rate", "value": dist, "units": currency})
            else:
                cloud_total += dist
        if cloud_total:
            breakdown.append({"name": "Cloud cost", "source": "cloud", "value": cloud_total, "units": currency})
        breakdown.sort(key=lambda x: x.get("value") or 0, reverse=True)
        return breakdown

    def _apply_breakdown_limit(self, breakdown, limit):
        """Apply top-N limiting with 'Other' aggregation."""
        if limit is None or len(breakdown) <= limit:
            return breakdown
        breakdown.sort(key=lambda x: x.get("value") or 0, reverse=True)
        top_entries = breakdown[:limit]
        rest = breakdown[limit:]
        other_total = sum((e.get("value") or Decimal("0") for e in rest), Decimal("0"))
        units = breakdown[0].get("units", "USD") if breakdown else "USD"
        if other_total:
            top_entries.append({"name": "Other", "source": "other", "value": other_total, "units": units})
        return top_entries

    def execute_query(self):  # noqa: C901
        """Execute query and return provided data.

        Returns:
            (Dict): Dictionary response of query params, data, and total

        """
        query_sum = self.initialize_totals()
        data = []

        with tenant_context(self.tenant):
            query = self.query_table.objects.filter(self.query_filter)
            if self.query_exclusions:
                query = query.exclude(self.query_exclusions)
            query = query.annotate(**self.annotations)
            group_by_value = self._get_group_by()

            query_group_by = ["date"] + group_by_value
            query_order_by = ["-date", self.order]

            query_data = query.values(*query_group_by).annotate(
                **{k: v for k, v in self.report_annotations.items() if k not in group_by_value}
            )

            if is_grouped_by_project(self.parameters):
                query_data = self._project_classification_annotation(query_data)
            if self._limit and query_data:
                query_data = self._group_by_ranks(query, query_data)
                order_by = self.parameters.get("order_by")
                if not order_by or set(order_by).intersection(["cost_total", "cost_total_distributed"]):
                    # https://issues.redhat.com/browse/COST-3901
                    # order_by[distributed_cost] is required for distributing platform cost,
                    # therefore others must be at the end.
                    # override implicit ordering when using ranked ordering.
                    query_order_by[-1] = "rank"

            # Populate the 'total' section of the API response
            if query.exists():
                aggregates = self._mapper.report_type_map.get("aggregates")
                metric_sum = query.aggregate(**aggregates)
                query_sum = {key: metric_sum.get(key) for key in aggregates}

            query_data, total_capacity = self.get_capacity(query_data)
            if total_capacity:
                query_sum.update(total_capacity)
                calculate_unused(query_sum)

            if self._delta:
                query_data = self.add_deltas(query_data, query_sum)

            query_data = self.order_by(query_data, query_order_by)

            for row in query_data:
                if tag_iterable := row.get("tags"):
                    row["tags"] = self.format_tags(tag_iterable)

            if self.is_csv_output:
                if self._report_type == "virtual_machines":
                    date_string = self.date_to_string(self.time_interval[0])
                    data = [{"date": date_string, "vm_names": query_data}]
                elif self.parameters.get("breakdown_limit") is not None and self._breakdown_table:
                    csv_breakdown_table = self._breakdown_table
                    csv_query = csv_breakdown_table.objects.filter(self.query_filter)
                    if self.query_exclusions:
                        csv_query = csv_query.exclude(self.query_exclusions)
                    csv_query = csv_query.annotate(**self.annotations)
                    csv_group_by = query_group_by + ["cost_model_rate_name"]
                    csv_data = csv_query.values(*csv_group_by).annotate(
                        **{k: v for k, v in self.report_annotations.items() if k not in csv_group_by}
                    )
                    csv_data = self.order_by(csv_data, query_order_by)
                    data = list(csv_data)
                else:
                    data = list(query_data)
            else:
                # Pass in a copy of the group by without the added
                # tag column name prefix
                groups = copy.deepcopy(query_group_by)
                groups.remove("date")
                data = self._apply_group_by(list(query_data), groups)
                data = self._transform_data(query_group_by, 0, data)

        sum_init = {"cost_units": self.currency}
        if self._mapper.usage_units_key:
            sum_init["usage_units"] = self._mapper.usage_units_key
        query_sum.update(sum_init)

        ordered_total = {
            total_key: query_sum[total_key] for total_key in self.report_annotations.keys() if total_key in query_sum
        }
        ordered_total.update(query_sum)

        self.query_sum = ordered_total
        self.query_data = data
        return self._format_query_response()

    # Capacity Calculations

    def get_capacity(self, query_data):
        """Calculate capacity & instance count for all nodes over the date range."""
        q_table = self._mapper.query_table
        LOG.debug(f"Using query table: {q_table}")
        query = q_table.objects.filter(self.query_filter)
        if self.query_exclusions:
            query = query.exclude(self.query_exclusions)
        with tenant_context(self.tenant):
            _class = NodeCapacity if is_grouped_by_node(self.parameters) else ClusterCapacity
            capacity = _class(self._mapper.report_type_map, query, self.resolution)
            if not capacity.capacity_aggregate:
                # short circuit for if the capacity dataclass in report provider map
                return query_data, {}
            capacity.populate_dataclass()
        for row in query_data:
            capacity.update_row(row, self.parameters.get("start_date"))
        return query_data, capacity.generate_query_sum()

    # Delta Calculations

    def add_deltas(self, query_data, query_sum):
        """Calculate and add cost deltas to a result set.

        Args:
            query_data (list) The existing query data from execute_query
            query_sum (list) The sum returned by calculate_totals

        Returns:
            (dict) query data with new with keys "value" and "percent"

        """
        if "__" in self._delta:
            return self.add_current_month_deltas(query_data, query_sum)
        else:
            return super().add_deltas(query_data, query_sum)

    def add_current_month_deltas(self, query_data, query_sum):
        """Add delta to the resultset using current month comparisons."""
        delta_field_one, delta_field_two = self._delta.split("__")

        for row in query_data:
            delta_value = Decimal(row.get(delta_field_one) or 0) - Decimal(row.get(delta_field_two) or 0)

            row["delta_value"] = delta_value
            try:
                row["delta_percent"] = (
                    Decimal(row.get(delta_field_one) or 0) / Decimal(row.get(delta_field_two) or 0) * Decimal(100)
                )
            except (DivisionByZero, ZeroDivisionError, InvalidOperation):
                row["delta_percent"] = None

        total_delta = Decimal(query_sum.get(delta_field_one) or 0) - Decimal(query_sum.get(delta_field_two) or 0)
        try:
            total_delta_percent = (
                Decimal(query_sum.get(delta_field_one) or 0)
                / Decimal(query_sum.get(delta_field_two) or 0)
                * Decimal(100)
            )
        except (DivisionByZero, ZeroDivisionError, InvalidOperation):
            total_delta_percent = None

        self.query_delta = {"value": total_delta, "percent": total_delta_percent}

        return query_data
