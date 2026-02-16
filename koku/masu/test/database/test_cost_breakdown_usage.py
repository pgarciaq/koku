#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for per-rate tiered usage cost execution (PR 4 — TDD RED phase).

Covers: T4.1–T4.6b (populate_usage_costs_by_name).
All tests are expected to FAIL until the production code is implemented.
"""
import pkgutil
from decimal import Decimal
from unittest.mock import patch

from django_tenants.utils import schema_context

from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
from masu.test import MasuTestCase
from reporting.models import OCPUsageLineItemDailySummary
from reporting.provider.ocp.models import OCPUsageReportPeriod


class PopulateUsageCostsByNameTest(MasuTestCase):
    """T4.1–T4.6b: Tests for the per-rate usage cost method."""

    def setUp(self):
        super().setUp()
        self.accessor = OCPReportDBAccessor(schema=self.schema)
        with schema_context(self.schema):
            self.report_period = OCPUsageReportPeriod.objects.filter(provider=self.ocp_provider).first()
            self.report_period_id = self.report_period.id if self.report_period else None

    def test_populate_usage_costs_by_name_creates_per_rate_rows(self):
        """T4.1: Each rate entry produces rows with distinct cost_model_rate_name."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU charge"},
            {"metric": "memory_gb_usage_per_hour", "value": Decimal("0.03"), "name": "Memory charge"},
        ]
        with self.accessor as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure",
                rates_by_name,
                "cpu",
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            names = set(
                OCPUsageLineItemDailySummary.objects.filter(
                    cost_model_rate_type="Infrastructure",
                    monthly_cost_type__isnull=True,
                    usage_start__gte=self.dh.this_month_start,
                )
                .values_list("cost_model_rate_name", flat=True)
                .distinct()
            )
        self.assertEqual(names, {"CPU charge", "Memory charge"})

    @patch(
        "masu.database.ocp_report_db_accessor.OCPReportDBAccessor"
        ".delete_line_item_daily_summary_entries_for_date_range_raw"
    )
    def test_populate_usage_costs_by_name_deletes_once_not_per_rate(self, mock_delete):
        """T4.2: Deletion happens once before the loop, not per rate."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
            {"metric": "memory_gb_usage_per_hour", "value": Decimal("0.03"), "name": "Memory"},
            {"metric": "storage_gb_usage_per_month", "value": Decimal("0.01"), "name": "Storage"},
        ]
        with self.accessor as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure",
                rates_by_name,
                "cpu",
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                self.report_period_id,
            )
        self.assertEqual(mock_delete.call_count, 1)

    def test_multiple_rates_same_metric_produce_separate_rows(self):
        """T4.3: Two CPU rates with different names produce two sets of rows."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "Base CPU"},
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.03"), "name": "Premium CPU"},
        ]
        with self.accessor as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure",
                rates_by_name,
                "cpu",
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            base_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_name="Base CPU",
                cost_model_rate_type="Infrastructure",
                monthly_cost_type__isnull=True,
            ).count()
            premium_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_name="Premium CPU",
                cost_model_rate_type="Infrastructure",
                monthly_cost_type__isnull=True,
            ).count()
        self.assertGreater(base_rows, 0)
        self.assertGreater(premium_rows, 0)
        self.assertEqual(base_rows, premium_rows)

    def test_populate_usage_costs_by_name_empty_rates_deletes_only(self):
        """T4.4: Empty rates list deletes existing rows and inserts nothing."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
        ]
        with self.accessor as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure",
                rates_by_name,
                "cpu",
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                self.report_period_id,
            )
        with self.accessor as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure",
                [],
                "cpu",
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            count = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="Infrastructure",
                monthly_cost_type__isnull=True,
                usage_start__gte=self.dh.this_month_start,
            ).count()
        self.assertEqual(count, 0)

    def test_zero_value_rate_skipped(self):
        """T4.5: A rate with value=0 does not produce rows."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
            {"metric": "memory_gb_usage_per_hour", "value": Decimal("0"), "name": "Memory (zero)"},
        ]
        with self.accessor as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure",
                rates_by_name,
                "cpu",
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            names = set(
                OCPUsageLineItemDailySummary.objects.filter(
                    cost_model_rate_type="Infrastructure",
                    monthly_cost_type__isnull=True,
                    usage_start__gte=self.dh.this_month_start,
                )
                .values_list("cost_model_rate_name", flat=True)
                .distinct()
            )
        self.assertEqual(names, {"CPU"})
        self.assertNotIn("Memory (zero)", names)

    def test_usage_costs_sql_has_rate_name_column(self):
        """T4.6: usage_costs.sql INSERT includes cost_model_rate_name."""
        sql = pkgutil.get_data("masu.database", "sql/openshift/cost_model/usage_costs.sql")
        sql_text = sql.decode("utf-8")
        self.assertIn("cost_model_rate_name", sql_text)

    def test_cluster_cost_per_hour_distributed_to_cpu_and_memory(self):
        """T4.6b: cluster_cost_per_hour in per-rate mode distributes across CPU and memory columns."""
        rates_by_name = [
            {"metric": "cluster_cost_per_hour", "value": Decimal("10.00"), "name": "Cluster hourly"},
        ]
        with self.accessor as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure",
                rates_by_name,
                "cpu",
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_name="Cluster hourly",
                cost_model_rate_type="Infrastructure",
                monthly_cost_type__isnull=True,
                usage_start__gte=self.dh.this_month_start,
            )
            self.assertTrue(rows.exists(), "cluster_cost_per_hour should produce rows")
            for row in rows:
                has_cost = (row.cost_model_cpu_cost or 0) != 0 or (row.cost_model_memory_cost or 0) != 0
                self.assertTrue(
                    has_cost,
                    f"cluster_cost_per_hour row should have cpu or memory cost, "
                    f"got cpu={row.cost_model_cpu_cost}, mem={row.cost_model_memory_cost}",
                )
            volume_sum = sum((r.cost_model_volume_cost or 0) for r in rows)
            self.assertEqual(volume_sum, 0, "cluster_cost_per_hour should not produce volume cost")
