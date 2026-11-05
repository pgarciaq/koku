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

from api.iam.test.iam_test_case import Customer
from api.provider.models import Provider
from api.provider.models import ProviderAuthentication
from cost_models.models import CostModel
from cost_models.models import CostModelMap
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


@override_settings(ROOT_URLCONF="masu.urls")
class EffectiveRatesTenantScopingTest(MasuTestCase):
    """#771 (V15-21): the provider resolution must be customer-qualified.

    Koku explicitly supports the same cluster_id across customers (shared
    authentication objects — pinned by
    api/provider/test/test_serializers.py::test_ocp_cluster_id_isolation_between_customers).
    The baseline resolved the provider by a global authentication scan with
    no customer predicate, so org B's request could return org A's provider
    and the tenant's cost-model lookup received a foreign provider ID —
    which ROS reship then trusts.
    """

    SHARED_CLUSTER_ID = "tenant-scoping-cluster-771"
    SCOPING_NS_B = "scoping-ns-b"

    def setUp(self):
        super().setUp()
        # ONE shared authentication row — koku's creation logic reuses
        # matching authentication objects across customers.
        self.shared_auth = ProviderAuthentication.objects.create(credentials={"cluster_id": self.SHARED_CLUSTER_ID})
        self.customer_b = Customer.objects.get(org_id="2222222")
        self.provider_a = Provider.objects.create(
            name="scoping-provider-a",
            type=Provider.PROVIDER_OCP,
            customer=self.customer,
            authentication=self.shared_auth,
        )
        self.provider_b = Provider.objects.create(
            name="scoping-provider-b",
            type=Provider.PROVIDER_OCP,
            customer=self.customer_b,
            authentication=self.shared_auth,
        )
        # Distinct rates per tenant so the response identifies the resolved
        # provider unambiguously (same-value collisions would mask a swap).
        self.tenant_cost_models = []
        for schema, provider, value, suffix in (
            ("org1234567", self.provider_a, 2.0, "a"),
            ("org2222222", self.provider_b, 1.0, "b"),
        ):
            with schema_context(schema):
                cm = CostModel.objects.create(
                    name=f"scoping-cm-{suffix}",
                    source_type=Provider.PROVIDER_OCP,
                    rates=[
                        {
                            "metric": {"name": "cpu_core_usage_per_hour"},
                            "tiered_rates": [{"unit": "USD", "value": value}],
                            "cost_type": "Supplementary",
                            "name": "CPU usage",
                        }
                    ],
                )
                CostModelMap.objects.create(cost_model_id=cm.uuid, provider_uuid=provider.uuid)
                self.tenant_cost_models.append((schema, cm.uuid))

    def tearDown(self):
        """Remove tenant-schema rows explicitly (schema_context writes may
        outlive the public-schema transaction rollback under KEEPDB)."""
        for schema, cm_uuid in self.tenant_cost_models:
            with schema_context(schema):
                CostModelMap.objects.filter(cost_model_id=cm_uuid).delete()
                CostModel.objects.filter(uuid=cm_uuid).delete()
        super().tearDown()

    @patch("koku.middleware.MASU", return_value=True)
    def test_org_b_resolves_own_provider(self, _):
        """RED on baseline: org B must get ITS provider and rates, not the
        first globally-matching provider's."""
        response = self.client.get(
            reverse("effective_rates"),
            {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": "2222222"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            body["provider_uuid"],
            str(self.provider_b.uuid),
            "org B must resolve its own provider — a global auth scan returns the first "
            "match and hands org B org A's provider (#771)",
        )
        self.assertEqual(
            body["configured_rates"]["cpu_core_usage_per_hour"]["supplementary"],
            1.0,
            "org B must see ITS cost model's rates, not the foreign provider's or empty "
            "(the (orgB, providerA) lookup queries the wrong combination, #771)",
        )

    @patch("koku.middleware.MASU", return_value=True)
    def test_org_a_resolves_own_provider(self, _):
        """Order-reversal control: org A gets its own provider and rates."""
        response = self.client.get(
            reverse("effective_rates"),
            {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": self.org_id},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["provider_uuid"], str(self.provider_a.uuid))
        self.assertEqual(body["configured_rates"]["cpu_core_usage_per_hour"]["supplementary"], 2.0)

    @patch("koku.middleware.MASU", return_value=True)
    def test_org_without_provider_gets_404(self, _):
        """A customer with no provider for the cluster gets 404 — no foreign
        provider, no foreign data."""
        response = self.client.get(
            reverse("effective_rates"),
            {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": "3333333"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("provider_uuid", response.json())

    @patch("koku.middleware.MASU", return_value=True)
    def test_namespace_aggregates_stay_tenant_scoped(self, _):
        """The namespace aggregates remain keyed to the requesting tenant's
        schema: a row seeded in org B's schema appears for B and not for A."""
        today = self.dh.today.date() if hasattr(self.dh.today, "date") else self.dh.today
        row_uuid = uuid.uuid4()
        with schema_context("org2222222"):
            OCPUsageLineItemDailySummary.objects.create(
                uuid=row_uuid,
                cluster_id=self.SHARED_CLUSTER_ID,
                usage_start=today,
                usage_end=today,
                namespace=self.SCOPING_NS_B,
                data_source="Pod",
            )
        try:
            resp_b = self.client.get(
                reverse("effective_rates"),
                {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": "2222222"},
            )
            self.assertEqual(resp_b.status_code, 200)
            self.assertIn(
                self.SCOPING_NS_B,
                resp_b.json()["namespace_aggregates"],
                "org B's aggregates must reflect org B's schema rows",
            )

            resp_a = self.client.get(
                reverse("effective_rates"),
                {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": self.org_id},
            )
            self.assertEqual(resp_a.status_code, 200)
            self.assertNotIn(
                self.SCOPING_NS_B,
                resp_a.json()["namespace_aggregates"],
                "org A must never see org B's namespace rows (tenant-scoped aggregates, #771)",
            )
        finally:
            with schema_context("org2222222"):
                OCPUsageLineItemDailySummary.objects.filter(uuid=row_uuid).delete()

    @patch("koku.middleware.MASU", return_value=True)
    def test_multiple_auth_rows_resolve_own_tenant(self, _):
        """Separate (identical-credentials) authentication rows per provider:
        the resolver must not stop at the first matching auth row when the
        customer filter misses — it continues to the requesting tenant's row
        (#771 review finding)."""
        auth_a = ProviderAuthentication.objects.create(credentials={"cluster_id": self.SHARED_CLUSTER_ID})
        Provider.objects.create(
            name="multi-auth-provider-a",
            type=Provider.PROVIDER_OCP,
            customer=self.customer,
            authentication=auth_a,
        )
        auth_b = ProviderAuthentication.objects.create(credentials={"cluster_id": self.SHARED_CLUSTER_ID})
        provider_b2 = Provider.objects.create(
            name="multi-auth-provider-b",
            type=Provider.PROVIDER_OCP,
            customer=self.customer_b,
            authentication=auth_b,
        )
        response = self.client.get(
            reverse("effective_rates"),
            {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": "2222222"},
        )
        self.assertEqual(response.status_code, 200)
        # Whichever of B's providers wins (both are B's), it must be B's —
        # never the first auth row's A provider.
        returned = response.json()["provider_uuid"]
        self.assertIn(
            returned,
            {str(self.provider_b.uuid), str(provider_b2.uuid)},
            "org B must resolve one of ITS providers across multiple auth rows (#771)",
        )

    @patch("koku.middleware.MASU", return_value=True)
    def test_name_ordering_does_not_decide_and_unowned_excluded(self, _):
        """The customer predicate decides, not provider-name ordering: with
        the foreign provider named to sort FIRST, org B still gets its own;
        and an unowned (customer=null) provider is never resolved (#771
        review finding)."""
        auth_u = ProviderAuthentication.objects.create(credentials={"cluster_id": self.SHARED_CLUSTER_ID})
        # A's provider named to sort before B's setUp providers.
        Provider.objects.create(
            name="aaa-scoping-provider-a2",
            type=Provider.PROVIDER_OCP,
            customer=self.customer,
            authentication=auth_u,
        )
        # An unowned provider on the same auth row.
        Provider.objects.create(
            name="aaa-unowned-provider",
            type=Provider.PROVIDER_OCP,
            customer=None,
            authentication=auth_u,
        )
        response = self.client.get(
            reverse("effective_rates"),
            {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": "2222222"},
        )
        self.assertEqual(response.status_code, 200)
        returned = response.json()["provider_uuid"]
        self.assertEqual(
            returned,
            str(self.provider_b.uuid),
            "the customer predicate must decide the resolution, not name ordering — and the "
            "unowned provider (customer=null, sorts first) must never be resolved for any org (#771)",
        )

        # And the unowned provider is not resolvable by org A either.
        response_a = self.client.get(
            reverse("effective_rates"),
            {"cluster_id": self.SHARED_CLUSTER_ID, "org_id": self.org_id},
        )
        self.assertEqual(response_a.status_code, 200)
        self.assertNotEqual(
            response_a.json()["provider_uuid"],
            Provider.objects.get(name="aaa-unowned-provider").uuid,
            "an unowned provider must never be resolved (#771)",
        )
