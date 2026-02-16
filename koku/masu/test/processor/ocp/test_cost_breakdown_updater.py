#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for rate name threading through OCPCostModelCostUpdater (PR 3+4 — TDD RED phase).

Covers: T3.7–T3.9c (updater rate_name threading),
        T4.7 (updater calls new per-rate method).
All tests are expected to FAIL until the production code is implemented.
"""
from decimal import Decimal
from unittest.mock import patch

from masu.processor.ocp.ocp_cost_model_cost_updater import OCPCostModelCostUpdater
from masu.test import MasuTestCase


class OCPCostModelCostUpdaterRateNameTest(MasuTestCase):
    """T3.7–T3.9c, T4.7: Tests for rate_name threading through the cost updater."""

    def _build_mock_accessor(self, mock_accessor_cls):
        """Configure a mock CostModelDBAccessor with rate-name properties."""
        mock_accessor = mock_accessor_cls.return_value.__enter__.return_value
        mock_accessor.infrastructure_rates = {"cpu_core_usage_per_hour": Decimal("0.05")}
        mock_accessor.supplementary_rates = {}
        mock_accessor.tag_infrastructure_rates = {}
        mock_accessor.tag_supplementary_rates = {}
        mock_accessor.tag_default_infrastructure_rates = {}
        mock_accessor.tag_default_supplementary_rates = {}
        mock_accessor.tag_usage_rates = {}
        mock_accessor.usage_rates = {}
        mock_accessor.monthly_cost_rates = {}
        mock_accessor.markup = {}
        mock_accessor.distribution_info = {"distribution_type": "cpu"}
        mock_accessor.metric_to_tag_params_map = {}
        mock_accessor.infrastructure_rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU charge"},
        ]
        mock_accessor.supplementary_rates_by_name = []
        mock_accessor.tag_rate_names = {}
        return mock_accessor

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_monthly_cost_sql")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_monthly_cost_passes_rate_name(self, mock_accessor_cls, mock_populate):
        """T3.7: _update_monthly_cost passes rate_name to populate_monthly_cost_sql."""
        mock_accessor = self._build_mock_accessor(mock_accessor_cls)
        mock_accessor.infrastructure_rates = {"node_cost_per_month": Decimal("100")}
        mock_accessor.infrastructure_rates_by_name = [
            {"metric": "node_cost_per_month", "value": Decimal("100"), "name": "Node charge"},
        ]

        updater = OCPCostModelCostUpdater(self.schema, self.ocp_provider)
        updater._update_monthly_cost(self.dh.this_month_start, self.dh.this_month_end)

        call_kwargs = mock_populate.call_args
        rate_name = call_kwargs.kwargs.get("rate_name") if call_kwargs.kwargs else None
        if rate_name is None and call_kwargs[1]:
            rate_name = call_kwargs[1].get("rate_name")
        self.assertEqual(rate_name, "Node charge")

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_monthly_cost_sql")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_multiple_monthly_rates_same_metric_both_applied(self, mock_accessor_cls, mock_populate):
        """T3.8: Two node_cost_per_month rates with different names both get SQL calls."""
        mock_accessor = self._build_mock_accessor(mock_accessor_cls)
        mock_accessor.infrastructure_rates = {"node_cost_per_month": Decimal("100")}
        mock_accessor.infrastructure_rates_by_name = [
            {"metric": "node_cost_per_month", "value": Decimal("50"), "name": "Base node"},
            {"metric": "node_cost_per_month", "value": Decimal("30"), "name": "Premium node"},
        ]

        updater = OCPCostModelCostUpdater(self.schema, self.ocp_provider)
        updater._update_monthly_cost(self.dh.this_month_start, self.dh.this_month_end)

        rate_names = set()
        for c in mock_populate.call_args_list:
            rn = c.kwargs.get("rate_name") if c.kwargs else None
            if rn is None and len(c) > 1 and c[1]:
                rn = c[1].get("rate_name")
            if rn in ("Base node", "Premium node"):
                rate_names.add(rn)
        self.assertEqual(rate_names, {"Base node", "Premium node"})

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_tag_usage_costs")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_tag_usage_costs_passes_tag_rate_names(self, mock_accessor_cls, mock_populate):
        """T3.9: _update_tag_usage_costs passes tag_rate_names to populate_tag_usage_costs."""
        mock_accessor = self._build_mock_accessor(mock_accessor_cls)
        mock_accessor.tag_infrastructure_rates = {"cpu_core_usage_per_hour": {"workload": {"jboss": Decimal("40")}}}
        mock_accessor.tag_rate_names = {"cpu_core_usage_per_hour": {"workload": "JBoss subscription"}}

        updater = OCPCostModelCostUpdater(self.schema, self.ocp_provider)
        updater._update_tag_usage_costs(self.dh.this_month_start, self.dh.this_month_end)

        call_kwargs = mock_populate.call_args
        self.assertIn("tag_rate_names", call_kwargs.kwargs)

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_tag_cost_sql")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_monthly_tag_based_cost_passes_rate_name(self, mock_accessor_cls, mock_populate):
        """T3.9b: _update_monthly_tag_based_cost passes name from tag_rate_names."""
        mock_accessor = self._build_mock_accessor(mock_accessor_cls)
        mock_accessor.tag_infrastructure_rates = {
            "node_cost_per_month": {"workload": {"jboss": Decimal("40")}},
        }
        mock_accessor.tag_rate_names = {
            "node_cost_per_month": {"workload": "JBoss tag rate"},
        }

        updater = OCPCostModelCostUpdater(self.schema, self.ocp_provider)
        updater._update_monthly_tag_based_cost(self.dh.this_month_start, self.dh.this_month_end)

        self.assertTrue(mock_populate.called, "populate_tag_cost_sql should have been called")
        call_kwargs = mock_populate.call_args
        rate_name = call_kwargs.kwargs.get("rate_name") if call_kwargs.kwargs else None
        self.assertEqual(rate_name, "JBoss tag rate")

    @patch("masu.database.ocp_report_db_accessor.trino_table_exists", return_value=False)
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_populate_tag_based_costs_passes_rate_name(self, mock_accessor_cls, mock_execute, mock_trino):
        """T3.9c: populate_tag_based_costs reads name from metric_to_tag_params_map and passes to SQL."""
        from masu.database.ocp_report_db_accessor import OCPReportDBAccessor

        metric_to_tag_params_map = {
            "vm_cost_per_month": [
                {
                    "rate_type": "Infrastructure",
                    "tag_key": "vm_type",
                    "default_rate": "5.00",
                    "value_rates": {"large": "10.00"},
                    "name": "VM monthly rate",
                }
            ],
        }
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_tag_based_costs(
                self.dh.this_month_start,
                self.dh.this_month_end,
                self.ocp_provider_uuid,
                metric_to_tag_params_map,
                cluster_params={},
            )
        for call_args in mock_execute.call_args_list:
            sql_params = call_args[0][2] if len(call_args[0]) > 2 else {}
            if "rate_name" in sql_params:
                self.assertEqual(sql_params["rate_name"], "VM monthly rate")
                break
        else:
            self.fail("No SQL call included rate_name parameter")

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_usage_costs_by_name")
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_usage_costs")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_updater_calls_populate_usage_costs_by_name(self, mock_accessor_cls, mock_old, mock_new):
        """T4.7: _update_usage_costs calls populate_usage_costs_by_name (not the old method)."""
        mock_accessor = self._build_mock_accessor(mock_accessor_cls)
        mock_accessor.infrastructure_rates = {"cpu_core_usage_per_hour": Decimal("0.05")}
        mock_accessor.infrastructure_rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
        ]

        updater = OCPCostModelCostUpdater(self.schema, self.ocp_provider)
        updater._update_usage_costs(self.dh.this_month_start, self.dh.this_month_end)

        mock_new.assert_called()
        mock_old.assert_not_called()
