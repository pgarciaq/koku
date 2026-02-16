# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests verifying cost_model_rate_name in SQL across all three SQL directories."""
import pkgutil

from masu.test import MasuTestCase


class CostBreakdownSQLPathsTest(MasuTestCase):
    """Tests that cost_model_rate_name appears in all relevant SQL templates."""

    def _assert_sql_has_rate_name(self, path):
        """Helper: assert that a SQL file includes cost_model_rate_name."""
        sql = pkgutil.get_data("masu.database", path).decode("utf-8")
        self.assertIn(
            "cost_model_rate_name",
            sql,
            f"{path} missing cost_model_rate_name",
        )

    def test_cloud_sql_usage_costs_has_rate_name(self):
        """Cloud usage_costs.sql includes cost_model_rate_name."""
        path = "sql/openshift/cost_model/usage_costs.sql"
        self._assert_sql_has_rate_name(path)

    def test_cloud_sql_tag_rates_have_rate_name(self):
        """Cloud infrastructure and supplementary tag rates SQL include cost_model_rate_name."""
        for path in [
            "sql/openshift/cost_model/infrastructure_tag_rates.sql",
            "sql/openshift/cost_model/supplementary_tag_rates.sql",
        ]:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)

    def test_cloud_sql_monthly_costs_have_rate_name(self):
        """Cloud monthly cost SQL files include cost_model_rate_name."""
        for path in [
            "sql/openshift/cost_model/monthly_cost_cluster_and_node.sql",
            "sql/openshift/cost_model/monthly_cost_persistentvolumeclaim.sql",
            "sql/openshift/cost_model/monthly_cost_virtual_machine.sql",
        ]:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)

    def test_cloud_sql_distribution_has_rate_name(self):
        """Cloud distribution SQL files include cost_model_rate_name."""
        for path in [
            "sql/openshift/cost_model/distribute_cost/distribute_platform_cost.sql",
            "sql/openshift/cost_model/distribute_cost/distribute_worker_cost.sql",
            "sql/openshift/cost_model/distribute_cost/distribute_unattributed_storage_cost.sql",
            "sql/openshift/cost_model/distribute_cost/distribute_unattributed_network_cost.sql",
        ]:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)

    def test_trino_sql_vm_rates_have_rate_name(self):
        """Trino VM rate SQL files include cost_model_rate_name."""
        for path in [
            "trino_sql/openshift/cost_model/hourly_cost_virtual_machine.sql",
            "trino_sql/openshift/cost_model/monthly_vm_core.sql",
        ]:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)

    def test_self_hosted_sql_vm_rates_have_rate_name(self):
        """Self-hosted VM rate SQL files include cost_model_rate_name."""
        for path in [
            "self_hosted_sql/openshift/cost_model/hourly_cost_virtual_machine.sql",
            "self_hosted_sql/openshift/cost_model/monthly_vm_core.sql",
        ]:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)

    def test_self_hosted_sql_gpu_distribution_has_rate_name(self):
        """Self-hosted GPU distribution SQL includes cost_model_rate_name."""
        path = "self_hosted_sql/openshift/cost_model/distribute_cost/distribute_unallocated_gpu_cost.sql"
        self._assert_sql_has_rate_name(path)

    def test_breakdown_summary_sql_has_rate_name(self):
        """Breakdown summary SQL files include cost_model_rate_name."""
        for path in [
            "sql/openshift/ui_summary/reporting_ocp_cost_breakdown_p.sql",
            "sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_project_p.sql",
            "sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_node_p.sql",
            "sql/openshift/ui_summary/reporting_ocp_vm_breakdown_p.sql",
        ]:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)
