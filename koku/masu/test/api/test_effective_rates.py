#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the effective_rates endpoint view."""
import uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.urls import reverse
from django_tenants.utils import schema_context

from masu.api import effective_rates as er
from masu.test import MasuTestCase
from reporting.provider.ocp.models import OCPUsageLineItemDailySummary


class EffectiveRatesCurrencyTest(SimpleTestCase):
    """Unit tests for currency extraction (no database)."""

    def test_extract_currency_from_first_tiered_rate(self):
        rates = [
            {
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"unit": "EUR", "value": 0.007}],
                "cost_type": "Supplementary",
            }
        ]
        self.assertEqual(er._extract_currency(rates), "EUR")

    def test_extract_currency_defaults_to_usd_when_missing(self):
        self.assertEqual(er._extract_currency([]), "USD")
        self.assertEqual(er._extract_currency([{"tiered_rates": []}]), "USD")
        self.assertEqual(er._extract_currency([{"tiered_rates": [{}]}]), "USD")


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
        self.assertIn("currency", body)
        self.assertIn("configured_rates", body)
        self.assertIn("namespace_aggregates", body)
        self.assertEqual(body["cluster_id"], self.ocp_cluster_id)
        self.assertEqual(body["provider_uuid"], self.ocp_provider_uuid)
        self.assertIn(body["distribution_type"], ("cpu", "memory"))
        self.assertEqual(body["currency"], "USD")

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


@override_settings(ROOT_URLCONF="masu.urls")
class EffectiveRatesGPUDistributedTest(MasuTestCase):
    """Test Cases verifying gpu_distributed rows are included in namespace_aggregates."""

    GPU_TEST_NS = "gpu-distributed-test-ns"
    MIXED_TEST_NS = "mixed-distributed-test-ns"

    def _seed_summary_row(self, **overrides):
        """Create an OCPUsageLineItemDailySummary row in the test tenant schema.

        Returns the UUID of the created row for cleanup.
        """
        today = self.dh.today.date() if hasattr(self.dh.today, "date") else self.dh.today
        row_uuid = uuid.uuid4()
        defaults = {
            "uuid": row_uuid,
            "cluster_id": self.ocp_cluster_id,
            "usage_start": today,
            "usage_end": today,
        }
        defaults.update(overrides)
        with schema_context(self.schema):
            OCPUsageLineItemDailySummary.objects.create(**defaults)
        return row_uuid

    def _cleanup_rows(self, uuids):
        """Delete seeded test rows."""
        with schema_context(self.schema):
            OCPUsageLineItemDailySummary.objects.filter(uuid__in=uuids).delete()

    def _call_effective_rates(self):
        """Call the effective_rates endpoint and return the parsed JSON body."""
        params = {"cluster_id": self.ocp_cluster_id, "org_id": self.org_id}
        response = self.client.get(reverse("effective_rates"), params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    @patch("koku.middleware.MASU", return_value=True)
    def test_gpu_distributed_rows_included_in_namespace_aggregates(self, _):
        """GPU-distributed rows (data_source='GPU') must appear in distributed_cost."""
        seeded = []
        try:
            seeded.append(
                self._seed_summary_row(
                    namespace=self.GPU_TEST_NS,
                    data_source="GPU",
                    cost_model_rate_type="gpu_distributed",
                    distributed_cost=Decimal("100.0"),
                )
            )
            body = self._call_effective_rates()
            ns_aggs = body["namespace_aggregates"]

            self.assertIn(self.GPU_TEST_NS, ns_aggs, "GPU namespace should appear in aggregates")
            self.assertGreaterEqual(
                ns_aggs[self.GPU_TEST_NS]["distributed_cost"],
                100.0,
                "distributed_cost must include gpu_distributed overhead",
            )
        finally:
            self._cleanup_rows(seeded)

    @patch("koku.middleware.MASU", return_value=True)
    def test_gpu_rows_do_not_inflate_cpu_memory_hours(self, _):
        """Adding GPU rows must not inflate CPU/memory usage or request hours."""
        seeded = []
        try:
            seeded.append(
                self._seed_summary_row(
                    namespace=self.MIXED_TEST_NS,
                    data_source="Pod",
                    cost_model_rate_type="Supplementary",
                    pod_usage_cpu_core_hours=Decimal("10.0"),
                    pod_request_memory_gigabyte_hours=Decimal("5.0"),
                )
            )
            seeded.append(
                self._seed_summary_row(
                    namespace=self.MIXED_TEST_NS,
                    data_source="GPU",
                    cost_model_rate_type="gpu_distributed",
                    distributed_cost=Decimal("50.0"),
                )
            )
            body = self._call_effective_rates()
            ns_aggs = body["namespace_aggregates"]

            self.assertIn(self.MIXED_TEST_NS, ns_aggs)
            agg = ns_aggs[self.MIXED_TEST_NS]
            self.assertAlmostEqual(
                agg["cpu_usage_hours"], 10.0, places=2, msg="CPU hours must not be inflated by GPU rows"
            )
            self.assertAlmostEqual(
                agg["mem_request_hours"], 5.0, places=2, msg="Memory hours must not be inflated by GPU rows"
            )
            self.assertGreaterEqual(agg["distributed_cost"], 50.0, msg="distributed_cost must include GPU overhead")
        finally:
            self._cleanup_rows(seeded)

    @patch("koku.middleware.MASU", return_value=True)
    def test_mixed_pod_and_gpu_distributed_costs_sum_correctly(self, _):
        """Pod-distributed and GPU-distributed costs must sum in distributed_cost."""
        seeded = []
        try:
            seeded.append(
                self._seed_summary_row(
                    namespace=self.MIXED_TEST_NS,
                    data_source="Pod",
                    cost_model_rate_type="platform_distributed",
                    distributed_cost=Decimal("200.0"),
                )
            )
            seeded.append(
                self._seed_summary_row(
                    namespace=self.MIXED_TEST_NS,
                    data_source="GPU",
                    cost_model_rate_type="gpu_distributed",
                    distributed_cost=Decimal("75.0"),
                )
            )
            body = self._call_effective_rates()
            ns_aggs = body["namespace_aggregates"]

            self.assertIn(self.MIXED_TEST_NS, ns_aggs)
            self.assertGreaterEqual(
                ns_aggs[self.MIXED_TEST_NS]["distributed_cost"],
                275.0,
                "distributed_cost must be the sum of Pod + GPU distributed costs",
            )
        finally:
            self._cleanup_rows(seeded)
