#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the effective_rates endpoint view."""
from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse

from masu.test import MasuTestCase


@override_settings(ROOT_URLCONF="masu.urls")
class EffectiveRatesTest(MasuTestCase):
    """Test Cases for the effective_rates endpoint."""

    @patch("koku.middleware.MASU", return_value=True)
    def test_get_effective_rates(self, _):
        """Test the GET effective_rates endpoint returns expected structure."""
        params = {"cluster_id": self.ocp_cluster_id, "org_id": self.org_id}
        response = self.client.get(reverse("effective_rates"), params)
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertIn("cluster_id", body)
        self.assertIn("provider_uuid", body)
        self.assertIn("distribution_type", body)
        self.assertIn("markup_pct", body)
        self.assertIn("configured_rates", body)
        self.assertIn("namespace_aggregates", body)
        self.assertEqual(body["cluster_id"], self.ocp_cluster_id)
        self.assertEqual(body["provider_uuid"], self.ocp_provider_uuid)
        self.assertIn(body["distribution_type"], ("cpu", "memory"))

    @patch("koku.middleware.MASU", return_value=True)
    def test_get_effective_rates_missing_cluster_id(self, _):
        """Test the GET effective_rates endpoint returns 400 when cluster_id is missing."""
        params = {"org_id": self.org_id}
        response = self.client.get(reverse("effective_rates"), params)
        body = response.json()

        self.assertEqual(response.status_code, 400)
        self.assertIn("Error", body)

    @patch("koku.middleware.MASU", return_value=True)
    def test_get_effective_rates_missing_org_id(self, _):
        """Test the GET effective_rates endpoint returns 400 when org_id is missing."""
        params = {"cluster_id": self.ocp_cluster_id}
        response = self.client.get(reverse("effective_rates"), params)
        body = response.json()

        self.assertEqual(response.status_code, 400)
        self.assertIn("Error", body)

    @patch("koku.middleware.MASU", return_value=True)
    def test_get_effective_rates_invalid_org_id(self, _):
        """Test the GET effective_rates endpoint returns 404 for unknown org_id."""
        params = {"cluster_id": self.ocp_cluster_id, "org_id": "9999999"}
        response = self.client.get(reverse("effective_rates"), params)

        self.assertEqual(response.status_code, 404)

    @patch("koku.middleware.MASU", return_value=True)
    def test_get_effective_rates_invalid_cluster_id(self, _):
        """Test the GET effective_rates endpoint returns 404 for unknown cluster_id."""
        params = {"cluster_id": "nonexistent-cluster", "org_id": self.org_id}
        response = self.client.get(reverse("effective_rates"), params)

        self.assertEqual(response.status_code, 404)

    @patch("koku.middleware.MASU", return_value=True)
    def test_get_effective_rates_namespace_aggregates_structure(self, _):
        """Test that namespace aggregates have the expected fields when data exists."""
        params = {"cluster_id": self.ocp_cluster_id, "org_id": self.org_id}
        response = self.client.get(reverse("effective_rates"), params)
        body = response.json()

        self.assertEqual(response.status_code, 200)
        ns_aggs = body["namespace_aggregates"]
        if ns_aggs:
            first_ns = next(iter(ns_aggs.values()))
            expected_keys = {
                "cost_model_cpu_cost",
                "cost_model_memory_cost",
                "infrastructure_cost",
                "distributed_cost",
                "cpu_usage_hours",
                "cpu_request_hours",
                "mem_usage_hours",
                "mem_request_hours",
            }
            self.assertEqual(set(first_ns.keys()), expected_keys)
