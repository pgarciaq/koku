#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the effective_rates endpoint view."""
import uuid
from datetime import date
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
from cost_models.models import PriceList
from cost_models.models import PriceListCostModelMap
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
                # #467 contract point 3: observed GPU spend travels with the rest.
                "cost_model_gpu_cost",
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
        #
        # The rates live on a mapped price list (#467): the endpoint now resolves
        # through CostModelDBAccessor(price_list_effective_on=start_date), which is
        # exactly what Masu applies. A cost model with inline `rates` and no price
        # list resolves to no rates at all — zero costs — so a fixture that only
        # set `rates` would pass this test for the wrong reason.
        self.tenant_cost_models = []
        self.tenant_price_lists = []
        for schema, provider, value, suffix in (
            ("org1234567", self.provider_a, 2.0, "a"),
            ("org2222222", self.provider_b, 1.0, "b"),
        ):
            with schema_context(schema):
                rates = [
                    {
                        "metric": {"name": "cpu_core_usage_per_hour"},
                        "tiered_rates": [{"unit": "USD", "value": value}],
                        "cost_type": "Supplementary",
                        "custom_name": "CPU usage",
                    }
                ]
                cm = CostModel.objects.create(
                    name=f"scoping-cm-{suffix}",
                    source_type=Provider.PROVIDER_OCP,
                    rates=rates,
                )
                CostModelMap.objects.create(cost_model_id=cm.uuid, provider_uuid=provider.uuid)
                price_list = PriceList.objects.create(
                    name=f"scoping-pl-{suffix}",
                    description="",
                    currency="USD",
                    effective_start_date=date(2020, 1, 1),
                    effective_end_date=date(2099, 12, 31),
                    rates=rates,
                )
                PriceListCostModelMap.objects.create(price_list=price_list, cost_model=cm, priority=1)
                self.tenant_cost_models.append((schema, cm.uuid))
                self.tenant_price_lists.append((schema, price_list.uuid))

    def tearDown(self):
        """Remove tenant-schema rows explicitly (schema_context writes may
        outlive the public-schema transaction rollback under KEEPDB)."""
        for schema, pl_uuid in self.tenant_price_lists:
            with schema_context(schema):
                PriceListCostModelMap.objects.filter(price_list_id=pl_uuid).delete()
                PriceList.objects.filter(uuid=pl_uuid).delete()
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


@override_settings(ROOT_URLCONF="masu.urls")
class EffectiveRatesRatesPassthroughTest(MasuTestCase):
    """#467: the payload must carry the cost model's rows as-is.

    Baseline `_parse_rates_list` kept only `tiered_rates[0].value` per metric and
    dropped `tag_rates` entirely, and `_get_configured_rates` read
    `cost_model.rates` directly instead of resolving through
    `CostModelDBAccessor(..., price_list_effective_on=start_date)`. Consequences
    pinned here: tag-only metrics (GPU in the UI, `project_per_month`) surfaced
    as a fake `$0` while their prices were never emitted, and a cost model whose
    prices live in mapped price lists (the on-prem layout: `rates == {}`) emitted
    nothing at all — which is why node/PVC savings were 0 on the demo cluster.
    """

    CLUSTER_ID = "rates-passthrough-cluster-467"
    NS = "rates-passthrough-ns"

    def setUp(self):
        super().setUp()
        self.auth = ProviderAuthentication.objects.create(credentials={"cluster_id": self.CLUSTER_ID})
        self.provider = Provider.objects.create(
            name="rates-passthrough-provider",
            type=Provider.PROVIDER_OCP,
            customer=self.customer,
            authentication=self.auth,
        )
        self.cost_model_uuids = []
        self.price_list_uuids = []
        self.summary_row_uuids = []

    def tearDown(self):
        """Remove tenant-schema rows explicitly: schema_context writes may outlive
        the public-schema transaction rollback under KEEPDB."""
        with schema_context(self.schema):
            PriceListCostModelMap.objects.filter(price_list_id__in=self.price_list_uuids).delete()
            PriceList.objects.filter(uuid__in=self.price_list_uuids).delete()
            CostModelMap.objects.filter(cost_model_id__in=self.cost_model_uuids).delete()
            CostModel.objects.filter(uuid__in=self.cost_model_uuids).delete()
            OCPUsageLineItemDailySummary.objects.filter(uuid__in=self.summary_row_uuids).delete()
        super().tearDown()

    # -- fixtures ---------------------------------------------------------

    def _make_cost_model(self, rates):
        """Map a cost model to this provider. Unmapped provider => no model at all."""
        with schema_context(self.schema):
            cm = CostModel.objects.create(
                name=f"rates-passthrough-cm-{uuid.uuid4().hex[:8]}",
                source_type=Provider.PROVIDER_OCP,
                rates=rates,
            )
            CostModelMap.objects.create(cost_model_id=cm.uuid, provider_uuid=self.provider.uuid)
        self.cost_model_uuids.append(cm.uuid)
        return cm

    def _attach_price_list(self, cost_model, rates, start, end, currency="USD", priority=1):
        with schema_context(self.schema):
            pl = PriceList.objects.create(
                name=f"rates-passthrough-pl-{uuid.uuid4().hex[:8]}",
                description="",
                currency=currency,
                effective_start_date=start,
                effective_end_date=end,
                rates=rates,
            )
            PriceListCostModelMap.objects.create(price_list=pl, cost_model_id=cost_model.uuid, priority=priority)
        self.price_list_uuids.append(pl.uuid)
        return pl

    def _this_month(self):
        return (self.dh.this_month_start.date(), self.dh.this_month_end.date())

    def _get(self, **overrides):
        params = {"cluster_id": self.CLUSTER_ID, "org_id": self.org_id}
        params.update(overrides)
        response = self.client.get(reverse("effective_rates"), params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    @staticmethod
    def _tiered(metric, value, cost_type="Supplementary", unit="USD"):
        return {
            "metric": {"name": metric},
            "tiered_rates": [{"unit": unit, "value": value}],
            "cost_type": cost_type,
            "description": "",
        }

    # -- RED: price lists are the rate source -----------------------------

    @patch("koku.middleware.MASU", return_value=True)
    def test_price_list_rates_surface_when_cost_model_rates_are_empty(self, _):
        """Live observation on the demo cluster: `demo-cm1` has `rates == {}` and the
        prices live in mapped price lists. Baseline emitted neither `rates` nor a
        populated `configured_rates`, which is why node/PVC savings stayed 0."""
        cm = self._make_cost_model([])
        start, end = self._this_month()
        self._attach_price_list(
            cm,
            [
                self._tiered("cpu_core_usage_per_hour", "0.3"),
                self._tiered("node_core_cost_per_month", "200.0"),
            ],
            start,
            end,
        )

        body = self._get()

        self.assertEqual(len(body["rates"]), 2, "price-list rates must reach the payload")
        self.assertEqual(
            {row["metric"]["name"] for row in body["rates"]},
            {"cpu_core_usage_per_hour", "node_core_cost_per_month"},
        )
        self.assertEqual(body["configured_rates"]["cpu_core_usage_per_hour"]["supplementary"], 0.3)
        self.assertEqual(
            body["configured_rates"]["node_core_cost_per_month"]["supplementary"],
            200.0,
            "node_core_cost_per_month must reach configured_rates or node savings stay 0",
        )

    @patch("koku.middleware.MASU", return_value=True)
    def test_price_list_is_resolved_for_the_requested_start_date(self, _):
        """Contract point 4: rates resolve for the requested window, not for today."""
        cm = self._make_cost_model([])
        last_start, last_end = self.dh.last_month_start.date(), self.dh.last_month_end.date()
        this_start, this_end = self._this_month()
        self._attach_price_list(
            cm, [self._tiered("cpu_core_usage_per_hour", "9.99")], last_start, last_end, priority=1
        )
        self._attach_price_list(
            cm, [self._tiered("cpu_core_usage_per_hour", "0.007")], this_start, this_end, priority=2
        )

        this_body = self._get(start_date=str(this_start), end_date=str(this_end))
        last_body = self._get(start_date=str(last_start), end_date=str(last_end))

        self.assertEqual(this_body["rates"][0]["tiered_rates"][0]["value"], 700000)
        self.assertEqual(
            last_body["rates"][0]["tiered_rates"][0]["value"],
            999000000,
            "the requested start_date must select the price list that covers it",
        )

    @patch("koku.middleware.MASU", return_value=True)
    @patch("masu.database.cost_model_db_accessor.is_feature_flag_enabled_by_schema", return_value=True)
    def test_disable_price_list_flag_falls_back_to_cost_model_rates(self, _flag, __):
        """The Unleash kill-switch must still win: flag on => CostModel.rates."""
        cm = self._make_cost_model([self._tiered("cpu_core_usage_per_hour", "0.5")])
        start, end = self._this_month()
        self._attach_price_list(cm, [self._tiered("cpu_core_usage_per_hour", "0.007")], start, end)

        body = self._get()

        self.assertEqual(len(body["rates"]), 1)
        self.assertEqual(body["rates"][0]["tiered_rates"][0]["value"], 50000000)
        self.assertEqual(body["configured_rates"]["cpu_core_usage_per_hour"]["supplementary"], 0.5)

    # -- RED: shape + money scale -----------------------------------------

    @patch("koku.middleware.MASU", return_value=True)
    def test_tiered_row_mirrors_serializer_shape_in_micro_cents(self, _):
        """Contract point 1 + 7: same shape CostModelSerializer emits, money in
        integer micro-cents (`1 dollar = 100_000_000`). `configured_rates` keeps
        its legacy float-dollar scale until #461 converts the whole payload."""
        cm = self._make_cost_model([])
        start, end = self._this_month()
        self._attach_price_list(cm, [self._tiered("cpu_core_usage_per_hour", "0.007")], start, end)

        body = self._get()

        self.assertEqual(
            body["rates"][0],
            {
                "metric": {"name": "cpu_core_usage_per_hour"},
                "description": "",
                "cost_type": "Supplementary",
                "tiered_rates": [{"unit": "USD", "value": 700000}],
            },
            "the row must mirror RateSerializer.to_representation, minus the internal rate_id",
        )
        self.assertEqual(
            body["configured_rates"]["cpu_core_usage_per_hour"]["supplementary"],
            0.007,
            "configured_rates keeps dollars in this window — two scales on one payload by design (#461)",
        )

    @patch("koku.middleware.MASU", return_value=True)
    def test_tag_only_metric_is_absent_from_configured_rates(self, _):
        """Contract point 2: a tag-priced metric must not appear as a fake `$0`.
        Baseline seeded every metric with 0.0 and dropped the tag prices."""
        cm = self._make_cost_model([])
        start, end = self._this_month()
        self._attach_price_list(
            cm,
            [
                self._tiered("cpu_core_usage_per_hour", "0.007"),
                {
                    "metric": {"name": "gpu_cost_per_month"},
                    "cost_type": "Infrastructure",
                    "description": "",
                    "custom_name": "gpu rates",
                    "tag_rates": {
                        "tag_key": "nvidia",
                        "tag_values": [
                            {"tag_value": "A100", "value": "70.0", "unit": "USD", "default": False},
                            {"tag_value": "H100", "value": "120.0", "unit": "USD", "default": True},
                        ],
                    },
                },
            ],
            start,
            end,
        )

        body = self._get()

        gpu = next(row for row in body["rates"] if row["metric"]["name"] == "gpu_cost_per_month")
        self.assertEqual(gpu["tag_rates"]["tag_key"], "nvidia")
        self.assertEqual(gpu["custom_name"], "gpu rates")
        self.assertNotIn("tiered_rates", gpu, "a tag row carries tag_rates, not an empty tier list")
        tag_values = {tv["tag_value"]: tv for tv in gpu["tag_rates"]["tag_values"]}
        self.assertEqual(tag_values["A100"]["value"], 7000000000)
        self.assertEqual(tag_values["H100"]["value"], 12000000000)
        self.assertTrue(tag_values["H100"]["default"])
        self.assertNotIn(
            "gpu_cost_per_month",
            body["configured_rates"],
            "omit a tag-only metric rather than asserting a $0 the tags contradict",
        )
        self.assertIn("cpu_core_usage_per_hour", body["configured_rates"])

    @patch("koku.middleware.MASU", return_value=True)
    def test_currency_comes_from_tag_rates_when_there_are_no_tiered_rates(self, _):
        """A GPU-only model priced in EUR must not claim USD just because no tier
        carries a unit."""
        cm = self._make_cost_model([])
        start, end = self._this_month()
        self._attach_price_list(
            cm,
            [
                {
                    "metric": {"name": "gpu_cost_per_month"},
                    "cost_type": "Infrastructure",
                    "description": "",
                    "tag_rates": {
                        "tag_key": "nvidia",
                        "tag_values": [{"tag_value": "A100", "value": "70.0", "unit": "EUR", "default": True}],
                    },
                }
            ],
            start,
            end,
            currency="EUR",
        )

        body = self._get()

        self.assertEqual(body["currency"], "EUR")
        self.assertEqual(body["rates"][0]["tag_rates"]["tag_values"][0]["value"], 7000000000)

    @patch("koku.middleware.MASU", return_value=True)
    def test_tier_usage_bounds_are_folded_not_dropped(self, _):
        """`RateSerializer` folds bare `usage_start`/`usage_end` into a `usage` dict.
        Keep that shape for the exotic windowed tier, and keep it off a tier whose
        bounds are null (every real koku fixture)."""
        cm = self._make_cost_model([])
        start, end = self._this_month()
        self._attach_price_list(
            cm,
            [
                {
                    "metric": {"name": "node_cost_per_month"},
                    "tiered_rates": [
                        {"unit": "USD", "value": "100.0", "usage_start": 1, "usage_end": 100},
                        {"unit": "USD", "value": "50.0", "usage_start": None, "usage_end": None},
                    ],
                    "cost_type": "Infrastructure",
                    "description": "",
                }
            ],
            start,
            end,
        )

        tiers = self._get()["rates"][0]["tiered_rates"]

        self.assertEqual(
            tiers[0],
            {"unit": "USD", "value": 10000000000, "usage": {"usage_start": 1, "usage_end": 100, "unit": "USD"}},
        )
        self.assertEqual(tiers[1], {"unit": "USD", "value": 5000000000})

    # -- RED: GPU spend aggregate -----------------------------------------
    @patch("koku.middleware.MASU", return_value=True)
    def test_gpu_cost_model_spend_reaches_namespace_aggregates(self, _):
        """Contract point 3: observed GPU spend, not a derived list price."""
        today = self.dh.today.date()
        row_uuid = uuid.uuid4()
        with schema_context(self.schema):
            OCPUsageLineItemDailySummary.objects.create(
                uuid=row_uuid,
                cluster_id=self.CLUSTER_ID,
                usage_start=today,
                usage_end=today,
                namespace=self.NS,
                data_source="GPU",
                cost_model_gpu_cost=Decimal("12.5"),
            )
        self.summary_row_uuids.append(row_uuid)

        body = self._get()

        self.assertIn(self.NS, body["namespace_aggregates"])
        self.assertEqual(body["namespace_aggregates"][self.NS]["cost_model_gpu_cost"], 12.5)

    # -- control: nothing configured --------------------------------------

    @patch("koku.middleware.MASU", return_value=True)
    def test_no_cost_model_emits_no_rates_and_no_fake_currency(self, _):
        """200 preserved, no invented `rates`, and no `"USD"` for a model that does
        not exist."""
        body = self._get()

        self.assertEqual(body["rates"], [])
        self.assertEqual(body["configured_rates"], {})
        self.assertIsNone(body["currency"])
