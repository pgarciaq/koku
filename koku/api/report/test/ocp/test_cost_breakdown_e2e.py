#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""End-to-end tests for cost breakdown feature (TDD RED phase).

Covers: T-E2E.1–T-E2E.4 (full pipeline: cost model → cost application → breakdown → API).
These tests require ALL 8 PRs to be merged. Expected to FAIL until then.
"""
from django.urls import reverse
from rest_framework.test import APIClient

from api.iam.test.iam_test_case import IamTestCase
from api.provider.models import Provider


class CostBreakdownE2ETest(IamTestCase):
    """End-to-end test: cost model → cost application → breakdown → API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cost_model_data = {
            "name": "E2E Test Cost Model",
            "source_type": Provider.PROVIDER_OCP,
            "distribution": "cpu",
            "markup": {"value": 10, "unit": "percent"},
            "rates": [
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
                    "cost_type": "Supplementary",
                },
                {
                    "name": "Node monthly",
                    "metric": {"name": "node_cost_per_month"},
                    "tiered_rates": [{"value": 100, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
            ],
            "currency": "USD",
        }

    def test_full_pipeline_rate_names_in_api_response(self):
        """T-E2E.1: API response contains breakdown with all three rate names."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data["meta"]["total"]
        usage = total["cost"]["usage"]
        self.assertIn("breakdown", usage)
        breakdown_names = {e["name"] for e in usage["breakdown"]}
        self.assertIn("CPU charge", breakdown_names)
        self.assertIn("Memory charge", breakdown_names)
        self.assertIn("Node monthly", breakdown_names)

    def test_full_pipeline_overhead_breakdown(self):
        """T-E2E.2: Platform distributed cost has per-rate-name breakdown."""
        url = reverse("reports-openshift-costs") + "?group_by[project]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data["meta"]["total"]
        cost = total["cost"]
        if "platform_distributed" in cost:
            pd = cost["platform_distributed"]
            self.assertIn("breakdown", pd)
            breakdown = pd["breakdown"]
            self.assertTrue(len(breakdown) > 0)
            for entry in breakdown:
                self.assertIn("name", entry)
                self.assertIn("source", entry)
                self.assertIn(entry["source"], ("rate", "cloud"))

    def test_full_pipeline_csv_breakdown(self):
        """T-E2E.3: CSV export with breakdown_limit includes rate name rows."""
        url = reverse("reports-openshift-costs") + "?breakdown_limit=10"
        client = APIClient()
        response = client.get(url, HTTP_ACCEPT="text/csv", **self.headers)
        content = response.content.decode("utf-8")
        self.assertIn("cost_model_rate_name", content)
        self.assertIn("CPU charge", content)
        self.assertIn("Memory charge", content)

    def test_full_pipeline_backward_compatible(self):
        """T-E2E.4: API response without breakdown_limit is backward compatible."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        self.assertIn("meta", data)
        self.assertIn("data", data)
        self.assertIn("total", data["meta"])
        cost = data["meta"]["total"]["cost"]
        for key in ["total", "usage"]:
            if key in cost:
                self.assertIn("value", cost[key])
                self.assertIn("units", cost[key])
