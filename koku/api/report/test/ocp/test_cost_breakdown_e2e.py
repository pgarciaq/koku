#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""End-to-end tests for cost breakdown feature (TDD RED phase).

Covers: T-E2E.1–T-E2E.4 (full pipeline: cost model → cost application → breakdown → API).
These tests require ALL 8 PRs to be merged. Expected to FAIL until then.
"""
from decimal import Decimal

from django.urls import reverse
from django_tenants.utils import schema_context
from rest_framework.test import APIClient

from api.iam.test.iam_test_case import IamTestCase
from api.provider.models import Provider
from cost_models.models import CostModel
from cost_models.models import CostModelMap
from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
from masu.processor.ocp.ocp_cost_model_cost_updater import OCPCostModelCostUpdater
from masu.util.common import SummaryRangeConfig


class CostBreakdownE2ETest(IamTestCase):
    """End-to-end test: cost model → cost application → breakdown → API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cost_model_rates = [
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
        ]

    def setUp(self):
        super().setUp()
        # Identify the OCP provider available in the test DB
        self.ocp_provider = Provider.objects.filter(type=Provider.PROVIDER_OCP).first()
        if not self.ocp_provider:
            self.skipTest("No OCP provider in test fixtures")

        # Create a cost model with named rates and map it to the provider
        with schema_context(self.schema_name):
            CostModelMap.objects.filter(provider_uuid=self.ocp_provider.uuid).delete()
            self.cost_model = CostModel.objects.create(
                name="E2E Breakdown Test CM",
                source_type=Provider.PROVIDER_OCP,
                rates=self.cost_model_rates,
                markup={"value": Decimal("10"), "unit": "percent"},
                currency="USD",
            )
            CostModelMap.objects.create(
                cost_model_id=self.cost_model.uuid,
                provider_uuid=self.ocp_provider.uuid,
            )

        # Apply costs using the updater pipeline (this runs all cost application steps)
        summary_range = SummaryRangeConfig(
            start_date=self.dh.this_month_start,
            end_date=self.dh.today,
        )
        updater = OCPCostModelCostUpdater(self.schema_name, self.ocp_provider)
        updater.update_summary_cost_model_costs(summary_range)

        # Populate breakdown summary tables (PR 6)
        with OCPReportDBAccessor(self.schema_name) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.ocp_provider.uuid)

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
