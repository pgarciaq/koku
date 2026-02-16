#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for rate name in CostModelDBAccessor and OCPReportDBAccessor (PR 3 — TDD RED phase).

Covers: T3.1–T3.6 (CostModelDBAccessor properties),
        T3.10–T3.14 (OCPReportDBAccessor SQL parameter threading).
All tests are expected to FAIL until the production code is implemented.
"""
from decimal import Decimal
from unittest.mock import patch

from django_tenants.utils import schema_context

from api.models import Provider
from masu.database.cost_model_db_accessor import CostModelDBAccessor
from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
from masu.test import MasuTestCase
from masu.test.database.helpers import ReportObjectCreator
from reporting.models import OCPUsageLineItemDailySummary


class CostModelDBAccessorRatesByNameTest(MasuTestCase):
    """T3.1–T3.6: Tests for rate-name-aware accessor properties."""

    def setUp(self):
        super().setUp()
        self.creator = ReportObjectCreator(self.schema)
        self.rates = [
            {
                "name": "CPU charge",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
            {
                "name": "Memory charge",
                "metric": {"name": "memory_gb_usage_per_hour"},
                "tiered_rates": [{"value": 0.03, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
        ]
        from cost_models.models import CostModelMap

        with schema_context(self.schema):
            CostModelMap.objects.filter(provider_uuid=self.ocp_provider_uuid).delete()
        self.cost_model = self.creator.create_cost_model(self.ocp_provider_uuid, Provider.PROVIDER_OCP, self.rates)

    def test_infrastructure_rates_by_name_returns_list(self):
        """T3.1: infrastructure_rates_by_name returns a list of {metric, value, name} dicts."""
        with CostModelDBAccessor(self.schema, self.ocp_provider_uuid) as accessor:
            rates = accessor.infrastructure_rates_by_name
        self.assertIsInstance(rates, list)
        self.assertEqual(len(rates), 2)
        metrics = {r["metric"] for r in rates}
        self.assertEqual(metrics, {"cpu_core_usage_per_hour", "memory_gb_usage_per_hour"})
        for r in rates:
            self.assertIn("name", r)
            self.assertIn("value", r)
            self.assertIn("metric", r)

    def test_infrastructure_rates_by_name_preserves_duplicates(self):
        """T3.2: Two rates for same metric both appear in the list."""
        # Use a single cost model with both rates for the same metric.
        # (Only one cost model can be mapped to a provider.)
        rates_with_dup_metric = [
            {
                "name": "Base CPU",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
            {
                "name": "Premium CPU",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.03, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
        ]
        # Replace the cost model from setUp with one containing duplicate-metric rates
        from cost_models.models import CostModelMap

        with schema_context(self.schema):
            CostModelMap.objects.filter(provider_uuid=self.ocp_provider_uuid).delete()
        self.creator.create_cost_model(self.ocp_provider_uuid, Provider.PROVIDER_OCP, rates_with_dup_metric)
        with CostModelDBAccessor(self.schema, self.ocp_provider_uuid) as accessor:
            by_name = accessor.infrastructure_rates_by_name
        cpu_rates = [r for r in by_name if r["metric"] == "cpu_core_usage_per_hour"]
        self.assertEqual(len(cpu_rates), 2)
        names = {r["name"] for r in cpu_rates}
        self.assertEqual(names, {"Base CPU", "Premium CPU"})

    def test_supplementary_rates_by_name(self):
        """T3.3: supplementary_rates_by_name returns supplementary rates only."""
        from cost_models.models import CostModelMap

        rates = [
            {
                "name": "CPU infra",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
            {
                "name": "CPU supp",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.02, "unit": "USD"}],
                "cost_type": "Supplementary",
            },
        ]
        with schema_context(self.schema):
            CostModelMap.objects.filter(provider_uuid=self.ocp_provider_uuid).delete()
        self.creator.create_cost_model(self.ocp_provider_uuid, Provider.PROVIDER_OCP, rates)
        with CostModelDBAccessor(self.schema, self.ocp_provider_uuid) as accessor:
            supp = accessor.supplementary_rates_by_name
        self.assertEqual(len(supp), 1)
        self.assertEqual(supp[0]["name"], "CPU supp")

    def test_tag_rate_names_mapping(self):
        """T3.4: tag_rate_names returns {metric: {tag_key: rate_name}} mapping."""
        from cost_models.models import CostModelMap

        rates = [
            {
                "name": "JBoss subscription",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tag_rates": {
                    "tag_key": "workload",
                    "tag_values": [{"tag_value": "jboss", "value": 40.0, "unit": "USD"}],
                },
                "cost_type": "Infrastructure",
            },
        ]
        with schema_context(self.schema):
            CostModelMap.objects.filter(provider_uuid=self.ocp_provider_uuid).delete()
        self.creator.create_cost_model(self.ocp_provider_uuid, Provider.PROVIDER_OCP, rates)
        with CostModelDBAccessor(self.schema, self.ocp_provider_uuid) as accessor:
            names = accessor.tag_rate_names
        self.assertEqual(names["cpu_core_usage_per_hour"]["workload"], "JBoss subscription")

    def test_metric_to_tag_params_map_includes_name(self):
        """T3.5: metric_to_tag_params_map entries include 'name' key."""
        from cost_models.models import CostModelMap

        rates = [
            {
                "name": "GPU tag rate",
                "metric": {"name": "gpu_request_per_gpu_hour"},
                "tag_rates": {
                    "tag_key": "gpu_type",
                    "tag_values": [{"tag_value": "a100", "value": 5.0, "unit": "USD", "default": True}],
                },
                "cost_type": "Infrastructure",
            },
        ]
        with schema_context(self.schema):
            CostModelMap.objects.filter(provider_uuid=self.ocp_provider_uuid).delete()
        self.creator.create_cost_model(self.ocp_provider_uuid, Provider.PROVIDER_OCP, rates)
        with CostModelDBAccessor(self.schema, self.ocp_provider_uuid) as accessor:
            tag_map = accessor.metric_to_tag_params_map
        for metric, params_list in tag_map.items():
            for params in params_list:
                self.assertIn("name", params)

    def test_existing_infrastructure_rates_unchanged(self):
        """T3.6: Existing infrastructure_rates property is not broken (regression guard)."""
        with CostModelDBAccessor(self.schema, self.ocp_provider_uuid) as accessor:
            old_rates = accessor.infrastructure_rates
        self.assertIsInstance(old_rates, dict)
        self.assertIn("cpu_core_usage_per_hour", old_rates)
        self.assertIsInstance(old_rates["cpu_core_usage_per_hour"], (int, float, Decimal))


class OCPReportDBAccessorRateNameTest(MasuTestCase):
    """T3.10–T3.14: Tests for rate_name parameter threading to SQL."""

    def setUp(self):
        super().setUp()
        self.accessor = OCPReportDBAccessor(schema=self.schema)

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    def test_populate_monthly_cost_sql_includes_rate_name_param(self, mock_execute):
        """T3.10: populate_monthly_cost_sql passes rate_name in SQL params."""
        with self.accessor as acc:
            acc.populate_monthly_cost_sql(
                "Node",
                "node_cost_per_month",
                Decimal("100"),
                self.dh.this_month_start,
                self.dh.this_month_end,
                "cpu",
                self.ocp_provider_uuid,
                rate_name="Node charge",
            )
        sql_params = mock_execute.call_args[0][2]
        self.assertEqual(sql_params["rate_name"], "Node charge")

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    def test_populate_monthly_cost_sql_rate_name_defaults_none(self, mock_execute):
        """T3.11: rate_name defaults to None (→ SQL NULL) when not provided."""
        with self.accessor as acc:
            acc.populate_monthly_cost_sql(
                "Node",
                "node_cost_per_month",
                Decimal("100"),
                self.dh.this_month_start,
                self.dh.this_month_end,
                "cpu",
                self.ocp_provider_uuid,
            )
        sql_params = mock_execute.call_args[0][2]
        self.assertIsNone(sql_params["rate_name"])

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    def test_populate_tag_cost_sql_includes_rate_name(self, mock_execute):
        """T3.12: populate_tag_cost_sql passes rate_name in SQL params."""
        case_dict = {
            "cost": ("cpu_case_sql", "memory_case_sql", "volume_case_sql"),
            "labels": "labels_sql",
        }
        with self.accessor as acc:
            acc.populate_tag_cost_sql(
                "Node",
                "node_cost_per_month",
                "workload",
                case_dict,
                self.dh.this_month_start,
                self.dh.this_month_end,
                "cpu",
                self.ocp_provider_uuid,
                rate_name="JBoss tag rate",
            )
        sql_params = mock_execute.call_args[0][2]
        self.assertEqual(sql_params["rate_name"], "JBoss tag rate")

    def test_monthly_cost_sql_writes_rate_name_to_db(self):
        """T3.13: After populate_monthly_cost_sql, line items have cost_model_rate_name set."""
        with self.accessor as acc:
            acc.populate_monthly_cost_sql(
                "Node",
                "node_cost_per_month",
                Decimal("100"),
                self.dh.this_month_start,
                self.dh.this_month_end,
                "cpu",
                self.ocp_provider_uuid,
                rate_name="Node charge",
            )
        with schema_context(self.schema):
            rows = OCPUsageLineItemDailySummary.objects.filter(
                monthly_cost_type="Node",
                cost_model_rate_type="Infrastructure",
                usage_start__gte=self.dh.this_month_start,
            )
            for row in rows:
                self.assertEqual(row.cost_model_rate_name, "Node charge")

    def test_tag_rate_sql_writes_rate_name_to_db(self):
        """T3.14: After populate_tag_usage_costs with tag_rate_names, line items have rate_name."""
        infrastructure_rates = {"cpu_core_usage_per_hour": {"workload": {"jboss": Decimal("40")}}}
        supplementary_rates = {}
        with self.accessor as acc:
            acc.populate_tag_usage_costs(
                infrastructure_rates,
                supplementary_rates,
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_cluster_id,
                tag_rate_names={"cpu_core_usage_per_hour": {"workload": "JBoss subscription"}},
            )
        with schema_context(self.schema):
            tag_rows = OCPUsageLineItemDailySummary.objects.filter(
                monthly_cost_type="Tag",
                usage_start__gte=self.dh.this_month_start,
                cluster_id=self.ocp_cluster_id,
            )
            for row in tag_rows:
                self.assertEqual(row.cost_model_rate_name, "JBoss subscription")
