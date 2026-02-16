#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for breakdown summary tables (PR 6 — TDD RED phase).

Covers: T6.1–T6.9 (breakdown model existence, population, cleanup, grouping).
All tests are expected to FAIL until the production code is implemented.
"""
from decimal import Decimal
from unittest.mock import patch

from django_tenants.utils import schema_context
from model_bakery import baker

from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
from masu.test import MasuTestCase
from masu.util.common import SummaryRangeConfig
from reporting.models import OCPUsageLineItemDailySummary


class BreakdownSummaryTableTest(MasuTestCase):
    """T6.1–T6.9: Tests for breakdown summary table models and population."""

    def setUp(self):
        super().setUp()
        self.accessor = OCPReportDBAccessor(schema=self.schema)
        self.summary_range = SummaryRangeConfig(
            start_date=self.dh.this_month_start,
            end_date=self.dh.this_month_end,
        )

    def test_breakdown_models_exist(self):
        """T6.1: All four breakdown models are importable."""
        from reporting.provider.ocp.models import OCPCostBreakdownByNodeP
        from reporting.provider.ocp.models import OCPCostBreakdownByProjectP
        from reporting.provider.ocp.models import OCPCostBreakdownP
        from reporting.provider.ocp.models import OCPVMBreakdownP

        self.assertTrue(hasattr(OCPCostBreakdownP, "cost_model_rate_name"))
        self.assertTrue(hasattr(OCPCostBreakdownByProjectP, "namespace"))
        self.assertTrue(hasattr(OCPCostBreakdownByNodeP, "node"))
        self.assertTrue(hasattr(OCPVMBreakdownP, "vm_name"))

    def test_breakdown_tables_partitioned(self):
        """T6.2: Breakdown tables use RANGE partitioning on usage_start."""
        from reporting.provider.ocp.models import OCPCostBreakdownP

        self.assertEqual(OCPCostBreakdownP.PartitionInfo.partition_type, "RANGE")
        self.assertEqual(OCPCostBreakdownP.PartitionInfo.partition_cols, ["usage_start"])

    def test_populate_breakdown_summary_tables(self):
        """T6.3: Breakdown summary tables are populated from line item data."""
        with self.accessor as acc:
            acc._populate_breakdown_summary_tables(self.summary_range, self.ocp_provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPCostBreakdownP

            rows = OCPCostBreakdownP.objects.filter(
                usage_start__gte=self.dh.this_month_start,
                source_uuid=self.ocp_provider_uuid,
            )
            self.assertTrue(rows.exists())
            rate_names = set(rows.values_list("cost_model_rate_name", flat=True))
            self.assertTrue(len(rate_names) > 0)

    def test_breakdown_by_project_includes_namespace(self):
        """T6.4: OCPCostBreakdownByProjectP rows carry namespace dimension."""
        with self.accessor as acc:
            acc._populate_breakdown_summary_tables(self.summary_range, self.ocp_provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPCostBreakdownByProjectP

            rows = OCPCostBreakdownByProjectP.objects.filter(
                usage_start__gte=self.dh.this_month_start,
            )
            namespaces = set(rows.values_list("namespace", flat=True))
            self.assertTrue(len(namespaces) > 0)

    def test_breakdown_cleanup_on_repopulate(self):
        """T6.5: Repopulating breakdown tables deletes old rows for the same date range and source."""
        from reporting.provider.ocp.models import OCPCostBreakdownP

        with self.accessor as acc:
            acc._populate_breakdown_summary_tables(self.summary_range, self.ocp_provider_uuid)
        with schema_context(self.schema):
            count_1 = OCPCostBreakdownP.objects.filter(source_uuid=self.ocp_provider_uuid).count()
        with self.accessor as acc:
            acc._populate_breakdown_summary_tables(self.summary_range, self.ocp_provider_uuid)
        with schema_context(self.schema):
            count_2 = OCPCostBreakdownP.objects.filter(source_uuid=self.ocp_provider_uuid).count()
        self.assertEqual(count_1, count_2)

    def test_breakdown_groups_by_rate_name(self):
        """T6.6: Breakdown table has separate rows for each (rate_type, rate_name) combination."""
        from reporting.provider.ocp.models import OCPCostBreakdownP

        with self.accessor as acc:
            acc._populate_breakdown_summary_tables(self.summary_range, self.ocp_provider_uuid)
        with schema_context(self.schema):
            combos = list(
                OCPCostBreakdownP.objects.filter(
                    usage_start__gte=self.dh.this_month_start,
                )
                .values("cost_model_rate_type", "cost_model_rate_name")
                .distinct()
            )
            rate_type_name_pairs = {(c["cost_model_rate_type"], c["cost_model_rate_name"]) for c in combos}
            infra_names = {n for t, n in rate_type_name_pairs if t == "Infrastructure" and n}
            self.assertGreaterEqual(len(infra_names), 2)

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._populate_breakdown_summary_tables")
    def test_populate_ui_summary_calls_breakdown(self, mock_breakdown):
        """T6.7: populate_ui_summary_tables also populates breakdown tables."""
        with self.accessor as acc:
            acc.populate_ui_summary_tables(self.summary_range, self.ocp_provider_uuid)
        mock_breakdown.assert_called_once()

    def test_vm_breakdown_populates_vm_name(self):
        """T6.8: OCPVMBreakdownP rows include vm_name extracted from labels."""
        with self.accessor as acc:
            acc._populate_breakdown_summary_tables(self.summary_range, self.ocp_provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPVMBreakdownP

            rows = OCPVMBreakdownP.objects.filter(
                usage_start__gte=self.dh.this_month_start,
                source_uuid=self.ocp_provider_uuid,
            )
            vm_names = set(rows.values_list("vm_name", flat=True))
            non_null_names = {n for n in vm_names if n is not None}
            self.assertTrue(len(non_null_names) > 0, "VM breakdown should have vm_name values")

    def test_breakdown_includes_tag_cost_type_rows(self):
        """T6.9: Breakdown summary includes rows with monthly_cost_type='Tag' (tag-based rates)."""
        with schema_context(self.schema):
            baker.make(
                OCPUsageLineItemDailySummary,
                usage_start=self.dh.this_month_start,
                monthly_cost_type="Tag",
                cost_model_rate_type="Infrastructure",
                cost_model_rate_name="JBoss subscription",
                cost_model_cpu_cost=Decimal("40.00"),
                source_uuid=self.ocp_provider_uuid,
                cluster_id=self.ocp_cluster_id,
            )
        with self.accessor as acc:
            acc._populate_breakdown_summary_tables(self.summary_range, self.ocp_provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPCostBreakdownP

            rows = OCPCostBreakdownP.objects.filter(
                cost_model_rate_name="JBoss subscription",
                source_uuid=self.ocp_provider_uuid,
            )
            self.assertTrue(rows.exists(), "Tag-based rate rows must appear in breakdown summary")
