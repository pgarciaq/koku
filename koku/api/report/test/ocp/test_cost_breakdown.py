#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for cost breakdown API layer (PR 7 — TDD RED phase).

Covers: T7.1–T7.14, T7.8b, T7.13b–T7.13f (serializer, query handler, CSV, OCP-on-cloud).
All tests are expected to FAIL until the production code is implemented.
"""
from unittest import TestCase

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.iam.test.iam_test_case import IamTestCase
from api.report.ocp.serializers import OCPCostQueryParamSerializer


class OCPCostSerializerBreakdownTest(TestCase):
    """T7.1–T7.3: Tests for breakdown_limit query parameter."""

    def _make_context(self, path="/api/cost-management/v1/reports/openshift/costs/"):
        """Build a minimal request context for the serializer."""
        from unittest.mock import Mock
        from unittest.mock import patch

        self._currency_patcher = patch("api.report.serializers.get_currency", return_value="USD")
        self._currency_patcher.start()
        self.addCleanup(self._currency_patcher.stop)
        request = Mock(path=path)
        return {"request": request}

    def test_breakdown_limit_accepted(self):
        """T7.1: breakdown_limit is accepted as an integer parameter and available in validated_data."""
        params = {
            "breakdown_limit": 5,
            "filter": {"resolution": "monthly", "time_scope_value": "-1", "time_scope_units": "month"},
        }
        serializer = OCPCostQueryParamSerializer(data=params, context=self._make_context())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        # Must actually appear in validated_data (DRF silently ignores unknown fields)
        self.assertIn("breakdown_limit", serializer.validated_data)
        self.assertEqual(serializer.validated_data["breakdown_limit"], 5)

    def test_breakdown_limit_rejects_zero(self):
        """T7.2: breakdown_limit < 1 is rejected."""
        params = {
            "breakdown_limit": 0,
            "filter": {"resolution": "monthly", "time_scope_value": "-1", "time_scope_units": "month"},
        }
        serializer = OCPCostQueryParamSerializer(data=params, context=self._make_context())
        self.assertFalse(serializer.is_valid())

    def test_breakdown_limit_rejects_over_100(self):
        """T7.3: breakdown_limit > 100 is rejected."""
        params = {
            "breakdown_limit": 101,
            "filter": {"resolution": "monthly", "time_scope_value": "-1", "time_scope_units": "month"},
        }
        serializer = OCPCostQueryParamSerializer(data=params, context=self._make_context())
        self.assertFalse(serializer.is_valid())


class OCPCostQueryHandlerBreakdownTest(IamTestCase):
    """T7.4–T7.14, T7.8b, T7.13b–T7.13f: Tests for breakdown data in API response."""

    def test_cost_response_includes_usage_breakdown(self):
        """T7.4: OCP cost response includes 'breakdown' array on usage cost."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        self.assertIn("breakdown", usage)
        breakdown = usage["breakdown"]
        self.assertIsInstance(breakdown, list)
        if breakdown:
            entry = breakdown[0]
            self.assertIn("name", entry)
            self.assertIn("source", entry)
            self.assertIn("value", entry)
            self.assertIn("units", entry)

    def test_cost_response_includes_overhead_breakdown(self):
        """T7.5: OCP cost response includes 'breakdown' on overhead costs (platform_distributed)."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        if "platform_distributed" in cost:
            pd = cost["platform_distributed"]
            self.assertIn("breakdown", pd)

    def test_breakdown_limit_limits_entries(self):
        """T7.6: breakdown_limit=2 returns top 2 + 'Other' aggregation."""
        url = reverse("reports-openshift-costs") + "?breakdown_limit=2"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        breakdown = usage.get("breakdown", [])
        self.assertLessEqual(len(breakdown), 3)
        if len(breakdown) == 3:
            self.assertEqual(breakdown[-1]["name"], "Other")

    def test_no_breakdown_limit_returns_full(self):
        """T7.7: Without breakdown_limit, full breakdown is returned."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        breakdown = usage.get("breakdown", [])
        other_entries = [e for e in breakdown if e.get("name") == "Other"]
        self.assertEqual(len(other_entries), 0)

    def test_per_row_breakdown_attached(self):
        """T7.8: Each date row in data array has breakdown on its cost categories."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()["data"]
        for date_entry in data:
            values = date_entry.get("values", date_entry.get("clusters", []))
            for val in values if isinstance(values, list) else []:
                cost = val.get("cost", {})
                usage = cost.get("usage", {})
                if usage.get("value", 0) > 0:
                    self.assertIn("breakdown", usage)

    def test_per_row_breakdown_respects_limit(self):
        """T7.8b: Per-row breakdown also applies breakdown_limit top-N with 'Other'."""
        url = reverse("reports-openshift-costs") + "?breakdown_limit=2"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()["data"]
        for date_entry in data:
            values = date_entry.get("values", date_entry.get("clusters", []))
            for val in values if isinstance(values, list) else []:
                cost = val.get("cost", {})
                usage = cost.get("usage", {})
                breakdown = usage.get("breakdown", [])
                if breakdown:
                    self.assertLessEqual(len(breakdown), 3)

    def test_null_named_entries_aggregated_as_cloud_cost(self):
        """T7.9: NULL-named overhead entries appear as 'Cloud cost' in breakdown."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        if "platform_distributed" in cost:
            breakdown = cost["platform_distributed"].get("breakdown", [])
            cloud_entries = [e for e in breakdown if e.get("source") == "cloud"]
            if cloud_entries:
                self.assertEqual(cloud_entries[0]["name"], "Cloud cost")

    def test_existing_response_fields_unchanged(self):
        """T7.10: Existing cost fields (value, units) are not altered by breakdown addition."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        for key in ["total", "usage", "raw"]:
            if key in cost:
                self.assertIn("value", cost[key])
                self.assertIn("units", cost[key])

    def test_csv_with_breakdown_includes_rate_name_column(self):
        """T7.11: CSV export with breakdown_limit includes cost_model_rate_name column."""
        url = reverse("reports-openshift-costs") + "?breakdown_limit=10"
        client = APIClient()
        response = client.get(url, HTTP_ACCEPT="text/csv", **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        content = response.content.decode("utf-8")
        self.assertIn("cost_model_rate_name", content)

    def test_csv_without_breakdown_no_rate_name_column(self):
        """T7.12: CSV export without breakdown_limit does not include cost_model_rate_name."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, HTTP_ACCEPT="text/csv", **self.headers)
        content = response.content.decode("utf-8")
        self.assertNotIn("cost_model_rate_name", content)

    def test_ocp_aws_includes_breakdown(self):
        """T7.13: OCP-on-AWS cost response includes breakdown from OCP breakdown tables."""
        url = reverse("reports-openshift-aws-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        usage = cost.get("usage", {})
        if usage.get("value", 0) > 0:
            self.assertIn("breakdown", usage)

    def test_provider_map_has_breakdown_views(self):
        """T7.13b: Provider map defines breakdown_views for costs, costs_by_project, and VMs."""
        from django_tenants.utils import tenant_context

        from api.models import Provider as ProviderModel
        from api.report.ocp.provider_map import OCPProviderMap

        try:
            from reporting.provider.ocp.models import OCPCostBreakdownByNodeP
            from reporting.provider.ocp.models import OCPCostBreakdownByProjectP
            from reporting.provider.ocp.models import OCPCostBreakdownP
            from reporting.provider.ocp.models import OCPVMBreakdownP
        except ImportError:
            self.fail("Breakdown models not yet created")

        with tenant_context(self.tenant):
            provider_map = OCPProviderMap(
                provider=ProviderModel.PROVIDER_OCP,
                report_type="costs",
                schema_name=self.schema_name,
            )

        self.assertIn("costs", provider_map.breakdown_views)
        self.assertEqual(provider_map.breakdown_views["costs"]["default"], OCPCostBreakdownP)
        self.assertEqual(provider_map.breakdown_views["costs"][("node",)], OCPCostBreakdownByNodeP)

        self.assertIn("costs_by_project", provider_map.breakdown_views)
        self.assertEqual(
            provider_map.breakdown_views["costs_by_project"]["default"],
            OCPCostBreakdownByProjectP,
        )

        self.assertIn("virtual_machines", provider_map.breakdown_views)
        self.assertEqual(provider_map.breakdown_views["virtual_machines"]["default"], OCPVMBreakdownP)

    def test_get_breakdown_table_resolves_by_group_by(self):
        """T7.13f: Query handler selects correct breakdown table based on group-by."""
        try:
            from reporting.provider.ocp.models import OCPCostBreakdownByNodeP
            from reporting.provider.ocp.models import OCPCostBreakdownP
        except ImportError:
            self.fail("Breakdown models not yet created")

        from api.report.ocp.query_handler import OCPReportQueryHandler
        from api.report.ocp.view import OCPCostView

        # No group-by → default breakdown table
        url = "?"
        query_params = self.mocked_query_params(url, OCPCostView)
        handler = OCPReportQueryHandler(query_params)
        self.assertEqual(handler._breakdown_table, OCPCostBreakdownP)

        # Node group-by → node breakdown table
        url = "?group_by[node]=*"
        query_params = self.mocked_query_params(url, OCPCostView)
        handler = OCPReportQueryHandler(query_params)
        self.assertEqual(handler._breakdown_table, OCPCostBreakdownByNodeP)

    def test_costs_by_project_includes_breakdown(self):
        """T7.13c: OCP costs_by_project response includes breakdown per project."""
        url = reverse("reports-openshift-costs") + "?group_by[project]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        self.assertIn("breakdown", usage)
        for date_entry in data.get("data", []):
            for project in date_entry.get("projects", []):
                cost = project.get("cost", {})
                proj_usage = cost.get("usage", {})
                if proj_usage.get("value", 0) > 0:
                    self.assertIn("breakdown", proj_usage)

    def test_node_group_by_includes_breakdown(self):
        """T7.13d: OCP costs grouped by node include breakdown."""
        url = reverse("reports-openshift-costs") + "?group_by[node]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        self.assertIn("breakdown", usage)

    def test_vm_view_includes_breakdown(self):
        """T7.13e: OCP virtual machine view includes breakdown."""
        url = reverse("reports-openshift-virtual-machines")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        usage = cost.get("usage", {})
        if usage.get("value", 0) > 0:
            self.assertIn("breakdown", usage)

    def test_tag_group_by_includes_breakdown(self):
        """T7.14: Tag group-by query includes per-rate breakdown."""
        url = reverse("reports-openshift-costs") + "?group_by[tag:app]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        for date_entry in data.get("data", []):
            for tag_group in date_entry.get("app", []):
                cost = tag_group.get("cost", {})
                usage = cost.get("usage", {})
                if usage.get("value", 0) > 0:
                    self.assertIn("breakdown", usage)

    def test_total_breakdown_scoped_to_entity_filter(self):
        """T7.15: meta.total.cost.usage.breakdown is scoped to the filtered entity.

        When group_by[project]=<name> is specified, the breakdown must be
        non-empty and its sum must not exceed usage.value (breakdown only
        includes named rates, so it is a subset of the full usage total).
        """
        url = reverse("reports-openshift-costs") + "?group_by[project]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()

        projects = []
        for date_entry in data.get("data", []):
            for proj in date_entry.get("projects", []):
                pname = proj.get("project")
                if pname and pname not in projects:
                    projects.append(pname)

        checked = 0
        for project in projects:
            url = reverse("reports-openshift-costs") + f"?group_by[project]={project}"
            response = client.get(url, **self.headers)
            rdata = response.json()
            usage = rdata.get("meta", {}).get("total", {}).get("cost", {}).get("usage", {})
            usage_value = float(usage.get("value", 0) or 0)
            breakdown = usage.get("breakdown", [])
            if usage_value and breakdown:
                breakdown_sum = float(sum(e.get("value", 0) for e in breakdown))
                self.assertGreater(breakdown_sum, 0, f"Project '{project}': breakdown is zero")
                self.assertLessEqual(
                    breakdown_sum,
                    usage_value * 1.001,
                    msg=f"Project '{project}': breakdown ({breakdown_sum}) exceeds usage ({usage_value})",
                )
                checked += 1
        self.assertGreater(checked, 0, "No projects had both usage and breakdown data")
