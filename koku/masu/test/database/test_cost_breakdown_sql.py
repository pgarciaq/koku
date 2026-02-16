#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for cost_model_rate_name in SQL templates (PR 8 — TDD RED phase).

These tests verify that all SQL templates include cost_model_rate_name.
All tests are expected to FAIL until the SQL files are modified.
"""
import pkgutil
from unittest import TestCase


class TrinoSelfHostedRateNameTest(TestCase):
    """T8.1–T8.3: Tests that SQL templates include cost_model_rate_name."""

    def _assert_sql_has_rate_name(self, path):
        """Helper: assert that a SQL file includes cost_model_rate_name."""
        sql = pkgutil.get_data("masu.database", path)
        sql_text = sql.decode("utf-8")
        self.assertIn(
            "cost_model_rate_name",
            sql_text,
            f"{path} missing cost_model_rate_name",
        )

    def test_trino_vm_sql_files_have_rate_name(self):
        """T8.1: All Trino VM SQL templates include cost_model_rate_name."""
        trino_files = [
            "trino_sql/openshift/cost_model/hourly_cost_virtual_machine.sql",
            "trino_sql/openshift/cost_model/hourly_vm_core.sql",
            "trino_sql/openshift/cost_model/monthly_vm_core.sql",
            "trino_sql/openshift/cost_model/hourly_cost_vm_tag_based.sql",
            "trino_sql/openshift/cost_model/hourly_vm_core_tag_based.sql",
            "trino_sql/openshift/cost_model/monthly_vm_core_tag_based.sql",
            "trino_sql/openshift/cost_model/monthly_project_tag_based.sql",
            "trino_sql/openshift/cost_model/monthly_cost_gpu.sql",
        ]
        for path in trino_files:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)

    def test_self_hosted_vm_sql_files_have_rate_name(self):
        """T8.2: All self-hosted VM SQL templates include cost_model_rate_name."""
        self_hosted_files = [
            "self_hosted_sql/openshift/cost_model/hourly_cost_virtual_machine.sql",
            "self_hosted_sql/openshift/cost_model/hourly_vm_core.sql",
            "self_hosted_sql/openshift/cost_model/monthly_vm_core.sql",
            "self_hosted_sql/openshift/cost_model/hourly_cost_vm_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/hourly_vm_core_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/monthly_vm_core_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/monthly_project_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/monthly_cost_gpu.sql",
        ]
        for path in self_hosted_files:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)

    def test_cloud_sql_files_have_rate_name(self):
        """T8.3: Cloud PostgreSQL SQL templates include cost_model_rate_name."""
        cloud_files = [
            "sql/openshift/cost_model/usage_costs.sql",
            "sql/openshift/cost_model/infrastructure_tag_rates.sql",
            "sql/openshift/cost_model/supplementary_tag_rates.sql",
            "sql/openshift/cost_model/default_infrastructure_tag_rates.sql",
            "sql/openshift/cost_model/default_supplementary_tag_rates.sql",
            "sql/openshift/cost_model/monthly_cost_cluster_and_node.sql",
            "sql/openshift/cost_model/monthly_cost_persistentvolumeclaim.sql",
            "sql/openshift/cost_model/monthly_cost_virtual_machine.sql",
            "sql/openshift/cost_model/node_cost_by_tag.sql",
            "sql/openshift/cost_model/monthly_cost_persistentvolumeclaim_by_tag.sql",
        ]
        for path in cloud_files:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)
