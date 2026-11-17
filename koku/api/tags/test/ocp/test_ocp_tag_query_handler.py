#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the Report Queries."""
from django.db.models import Q
from django_tenants.utils import tenant_context

from api.functions import JSONBObjectKeys
from api.iam.test.iam_test_case import IamTestCase
from api.provider.models import Provider
from api.query_filter import QueryFilter
from api.query_filter import QueryFilterCollection
from api.tags.ocp.queries import OCPTagQueryHandler
from api.tags.ocp.view import OCPTagView
from api.utils import DateHelper
from reporting.models import OCPStorageVolumeLabelSummary
from reporting.models import OCPUsageLineItemDailySummary
from reporting.models import OCPUsagePodLabelSummary
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.ocp.models import OCPTagsValues


class OCPTagQueryHandlerTest(IamTestCase):
    """Tests for the OCP report query handler."""

    @classmethod
    def setUpClass(cls):
        """Set up the test class."""
        super().setUpClass()
        cls.dh = DateHelper()

    def test_execute_query_no_query_parameters(self):
        """Test that the execute query runs properly with no query."""
        url = "?"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        query_output = handler.execute_query()
        self.assertIsNotNone(query_output.get("data"))
        self.assertEqual(handler.time_scope_units, "day")
        self.assertEqual(handler.time_scope_value, -10)

    def test_execute_query_wildcard_value_parameters(self):
        """Test that get tag values runs properly with value query as wildcard."""
        key = "version"
        value = "*"
        url = f"?filter[value]={value}"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        handler.key = key
        with tenant_context(self.tenant):
            storage_tags = (
                OCPTagsValues.objects.filter(key__exact=key, value__icontains="").values("value").distinct().all()
            )
            tag_values = [tag.get("value") for tag in storage_tags]
        expected = {"key": key, "values": tag_values}
        result = handler.get_tag_values()
        self.assertEqual(result[0].get("key"), expected.get("key"))
        self.assertEqual(sorted(result[0].get("values")), sorted(expected.get("values")))

    def test_execute_query_10_day_parameters(self):
        """Test that the execute query runs properly with 10 day query."""
        url = "?filter[time_scope_units]=day&filter[time_scope_value]=-10&filter[resolution]=daily"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        query_output = handler.execute_query()
        self.assertIsNotNone(query_output.get("data"))
        self.assertEqual(handler.time_scope_units, "day")
        self.assertEqual(handler.time_scope_value, -10)

    def test_execute_query_30_day_parameters(self):
        """Test that the execute query runs properly with 30 day query."""
        url = "?filter[time_scope_units]=day&filter[time_scope_value]=-30&filter[resolution]=daily"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        query_output = handler.execute_query()
        self.assertIsNotNone(query_output.get("data"))
        self.assertEqual(handler.time_scope_units, "day")
        self.assertEqual(handler.time_scope_value, -30)

    def test_execute_query_10_day_parameters_only_keys(self):
        """Test that the execute query runs properly with 10 day query."""
        url = "?filter[time_scope_units]=day&filter[time_scope_value]=-10&filter[resolution]=daily&key_only=True"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        query_output = handler.execute_query()
        self.assertIsNotNone(query_output.get("data"))
        self.assertEqual(handler.time_scope_units, "day")
        self.assertEqual(handler.time_scope_value, -10)

    def test_execute_query_month_parameters(self):
        """Test that the execute query runs properly with single month query."""
        url = "?filter[time_scope_units]=month&filter[time_scope_value]=-1&filter[resolution]=monthly"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        query_output = handler.execute_query()
        self.assertIsNotNone(query_output.get("data"))
        self.assertEqual(handler.time_scope_units, "month")
        self.assertEqual(handler.time_scope_value, -1)

    def test_execute_query_two_month_parameters(self):
        """Test that the execute query runs properly with two month query."""
        url = "?filter[time_scope_units]=month&filter[time_scope_value]=-2&filter[resolution]=monthly"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        query_output = handler.execute_query()
        self.assertIsNotNone(query_output.get("data"))
        self.assertEqual(handler.time_scope_units, "month")
        self.assertEqual(handler.time_scope_value, -2)

    def test_execute_query_for_project(self):
        """Test that the execute query runs properly with project query."""
        namespace = None
        with tenant_context(self.tenant):
            namespace_obj = OCPUsageLineItemDailySummary.objects.values("namespace").first()
            namespace = namespace_obj.get("namespace")

        url = f"?filter[time_scope_units]=day&filter[time_scope_value]=-10&filter[resolution]=daily&filter[project]={namespace}"  # noqa: E501
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        query_output = handler.execute_query()
        self.assertIsNotNone(query_output.get("data"))
        self.assertEqual(handler.time_scope_units, "day")
        self.assertEqual(handler.time_scope_value, -10)

    def test_get_tag_keys_filter_true(self):
        """Test that not all tag keys are returned with a filter."""
        # OCP vm tag key (enabled by default)
        vm_tag_key = "vm_kubevirt_io_name"
        url = (
            "?filter[time_scope_units]=month&filter[time_scope_value]=-2"
            "&filter[resolution]=monthly&filter[enabled]=True"
        )
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)

        with tenant_context(self.tenant):
            usage_tag_keys = list(
                OCPUsageLineItemDailySummary.objects.filter(usage_start__lte=self.dh.this_month_start)
                .annotate(tag_keys=JSONBObjectKeys("pod_labels"))
                .values_list("tag_keys", flat=True)
                .distinct()
                .all()
            )
            storage_tag_keys = list(
                OCPUsageLineItemDailySummary.objects.filter(usage_start__lte=self.dh.this_month_start)
                .annotate(tag_keys=JSONBObjectKeys("volume_labels"))
                .values_list("tag_keys", flat=True)
                .distinct()
                .all()
            )
            tag_keys = list(set(usage_tag_keys + storage_tag_keys))

        # The tag handler only returns enabled keys
        # this vm key bypasses the enabled model so
        # it should be removed from this test
        tag_keys.remove(vm_tag_key)
        result = handler.get_tag_keys(filters=True)
        self.assertEqual(sorted(result), sorted(tag_keys))
        self.assertNotIn("disabled", result)
        self.assertNotIn("disabled", tag_keys)
        self.assertIn("app", result)
        self.assertIn("app", tag_keys)

        url = (
            "?filter[time_scope_units]=month&filter[time_scope_value]=-2"
            "&filter[resolution]=monthly&filter[enabled]=true"
        )
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        result = handler.get_tag_keys(filters=True)
        self.assertEqual(sorted(result), sorted(tag_keys))

    def test_get_tag_keys_filter_false(self):
        """Test that all tag keys are returned with no filter."""
        # OCP vm tag key (enabled by default)
        vm_tag_key = "vm_kubevirt_io_name"
        url = "?filter[time_scope_units]=month&filter[time_scope_value]=-2&filter[resolution]=monthly"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)

        with tenant_context(self.tenant):
            usage_tag_keys = list(
                OCPUsageLineItemDailySummary.objects.annotate(tag_keys=JSONBObjectKeys("pod_labels"))
                .values_list("tag_keys", flat=True)
                .distinct()
                .all()
            )
            storage_tag_keys = list(
                OCPUsageLineItemDailySummary.objects.annotate(tag_keys=JSONBObjectKeys("volume_labels"))
                .values_list("tag_keys", flat=True)
                .distinct()
                .all()
            )
            tag_keys = list(set(usage_tag_keys + storage_tag_keys))

        result = handler.get_tag_keys(filters=False)
        # The tag handler only returns enabled keys
        # this vm key bypasses the enabled model so
        # it should be removed from this test
        tag_keys.remove(vm_tag_key)

        self.assertEqual(sorted(result), sorted(tag_keys))
        self.assertNotIn("disabled", result)
        self.assertNotIn("disabled", tag_keys)
        self.assertIn("app", result)
        self.assertIn("app", tag_keys)

    def test_get_tag_type_filter_pod(self):
        """Test that all usage tags are returned with pod type filter."""
        # OCP vm tag key (enabled by default)
        vm_tag_key = "vm_kubevirt_io_name"
        url = "?filter[time_scope_units]=month&filter[time_scope_value]=-2&filter[resolution]=monthly&filter[type]=pod"  # noqa: E501
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)

        with tenant_context(self.tenant):
            usage_tag_keys = list(
                OCPUsageLineItemDailySummary.objects.annotate(tag_keys=JSONBObjectKeys("pod_labels"))
                .values_list("tag_keys", flat=True)
                .distinct()
                .all()
            )
        tag_keys = list(set(usage_tag_keys))
        # The tag handler only returns enabled keys
        # this vm key bypasses the enabled model so
        # it should be removed from this test
        tag_keys.remove(vm_tag_key)

        result = handler.get_tag_keys(filters=False)
        self.assertEqual(sorted(result), sorted(tag_keys))
        self.assertNotIn("disabled", result)
        self.assertNotIn("disabled", tag_keys)
        self.assertIn("app", result)
        self.assertIn("app", tag_keys)

        result = handler.get_tag_keys(filters=True)
        self.assertEqual(sorted(result), sorted(tag_keys))

    def test_get_tag_type_filter_storage(self):
        """Test that all storage tags are returned with storage type filter."""
        url = "?filter[time_scope_units]=month&filter[time_scope_value]=-2&filter[resolution]=monthly&filter[type]=storage"  # noqa: E501
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)

        with tenant_context(self.tenant):
            tag_keys = list(OCPStorageVolumeLabelSummary.objects.values_list("key", flat=True).distinct().all())

        result = handler.get_tag_keys(filters=False)
        self.assertEqual(sorted(result), sorted(tag_keys))

    def test_get_tag_cluster_filter(self):
        """Test that tags from a cluster are returned with the cluster filter."""
        # Note: By default the OCP tag handler only grabs enabled keys
        url = "?filter[cluster]=OCP-on-AWS&filter[type]=storage"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)

        with tenant_context(self.tenant):
            storage_tag_keys = list(
                OCPStorageVolumeLabelSummary.objects.filter(report_period__cluster_id__contains="OCP-on-AWS")
                .values_list("key", flat=True)
                .distinct()
                .all()
            )
            enabled = list(
                EnabledTagKeys.objects.filter(provider_type=Provider.PROVIDER_OCP).values_list("key", flat=True).all()
            )
            tag_keys = [tag for tag in storage_tag_keys if tag in enabled]

        result = handler.get_tag_keys()
        self.assertEqual(sorted(result), sorted(tag_keys))

    def test_get_tags_for_key_filter(self):
        """Test that get tags runs properly with key query."""
        key = "app"
        url = f"?filter[key]={key}"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        with tenant_context(self.tenant):
            storage_tags = (
                OCPStorageVolumeLabelSummary.objects.filter(key__exact=key).values("values").distinct().all()
            )
            storage_values = [value for tag in storage_tags for value in tag.get("values")]
            usage_tags = OCPUsagePodLabelSummary.objects.filter(key__exact=key).values("values").distinct().all()
            usage_values = [value for tag in usage_tags for value in tag.get("values")]
            # remove duplicates from the values
            tag_values = list(dict.fromkeys(storage_values + usage_values))
        expected = {"key": key, "values": tag_values}
        result = handler.get_tags()
        self.assertEqual(result[0].get("key"), expected.get("key"))
        self.assertEqual(sorted(result[0].get("values")), sorted(expected.get("values")))

    def test_get_tag_values_for_value_filter(self):
        """Test that get tag values runs properly with value query."""
        key = "version"
        value = "Andromeda"
        url = f"?filter[value]={value}"
        query_params = self.mocked_query_params(url, OCPTagView)
        handler = OCPTagQueryHandler(query_params)
        handler.key = key
        with tenant_context(self.tenant):
            storage_tags = (
                OCPTagsValues.objects.filter(key__exact=key, value__icontains=value).values("value").distinct().all()
            )
            tag_values = [tag.get("value") for tag in storage_tags]
        expected = {"key": key, "values": tag_values}
        result = handler.get_tag_values()
        self.assertEqual(result[0].get("key"), expected.get("key"))
        self.assertEqual(sorted(result[0].get("values")), sorted(expected.get("values")))

    def test_get_tag_values_for_value_filter_partial_match(self):
        """Test that the execute query runs properly with value query."""
        key = "version"
        value = "a"
        url = f"/version/?filter[value]={value}"
        query_params = self.mocked_query_params(url, OCPTagView)
        # the mocked query parameters dont include the key from the url so it needs to be added
        query_params.kwargs = {"key": key}
        handler = OCPTagQueryHandler(query_params)
        with tenant_context(self.tenant):
            storage_tags = (
                OCPTagsValues.objects.filter(key__exact=key, value__icontains=value).values("value").distinct().all()
            )
            storage_values = [tag.get("value") for tag in storage_tags]
            usage_tags = (
                OCPTagsValues.objects.filter(key__exact=key, value__icontains=value).values("value").distinct().all()
            )
            usage_values = [tag.get("value") for tag in usage_tags]
            # remove duplicates from the values
            tag_values = list(dict.fromkeys(storage_values + usage_values))
        expected = {"key": key, "values": tag_values}
        result = handler.get_tag_values()
        self.assertEqual(result[0].get("key"), expected.get("key"))
        self.assertEqual(sorted(result[0].get("values")), sorted(expected.get("values")))

    def test_get_tag_values_for_value_filter_RBAC_node(self):
        """Test that the execute query runs properly with value query and an RBAC restriction on node.

        The access must go through ``mocked_query_params``: its mocked request
        skips the identity middleware, so a ``RbacPermissions`` decorator alone
        leaves ``QueryParameters.access`` empty and the assertion would silently
        be about the unfiltered result.
        """
        key = "app"
        value = "c"
        url = f"/app/?filter[value]={value}"
        query_params = self.mocked_query_params(url, OCPTagView, access={"openshift.node": {"read": ["node_0"]}})
        # the mocked query parameters dont include the key from the url so it needs to be added
        query_params.kwargs = {"key": key}
        handler = OCPTagQueryHandler(query_params)
        with tenant_context(self.tenant):
            tags = (
                OCPTagsValues.objects.filter(key__exact=key, value__icontains=value, nodes__overlap=["node_0"])
                .values("value")
                .distinct()
                .all()
            )
            tag_values = [tag.get("value") for tag in tags]
        # the fixture must exercise the filter, not an empty-vs-empty comparison
        self.assertTrue(tag_values)

        expected = {"key": key, "values": tag_values}
        result = handler.get_tag_values()
        self.assertEqual(result[0].get("key"), expected.get("key"))
        self.assertEqual(sorted(result[0].get("values")), sorted(expected.get("values")))

    def test_get_tag_values_for_value_filter_RBAC_cluster(self):
        """Test that the execute query runs properly with value query and an RBAC restriction on cluster."""
        key = "app"
        value = "b"
        access = ["OCP-on-Prem"]
        url = f"/app/?filter[value]={value}"
        query_params = self.mocked_query_params(url, OCPTagView, access={"openshift.cluster": {"read": access}})
        # the mocked query parameters dont include the key from the url so it needs to be added
        query_params.kwargs = {"key": key}
        handler = OCPTagQueryHandler(query_params)
        with tenant_context(self.tenant):
            tags = (
                OCPTagsValues.objects.filter(key__exact=key, value__icontains=value)
                .filter(Q(cluster_ids__overlap=access) | Q(cluster_aliases__overlap=access))
                .values("value")
                .distinct()
                .all()
            )
            tag_values = [tag.get("value") for tag in tags]
        # the fixture must exercise the filter, not an empty-vs-empty comparison
        self.assertTrue(tag_values)

        expected = {"key": key, "values": tag_values}
        result = handler.get_tag_values()
        self.assertEqual(result[0].get("key"), expected.get("key"))
        self.assertEqual(sorted(result[0].get("values")), sorted(expected.get("values")))

    def test_set_access_filters(self):
        """Test that the execute query runs properly with value query and an RBAC restriction on cluster."""
        key = "app"
        value = "b"
        url = f"/app/?filter[value]={value}"
        query_params = self.mocked_query_params(url, OCPTagView)
        # the mocked query parameters dont include the key from the url so it needs to be added
        query_params.kwargs = {"key": key}
        handler = OCPTagQueryHandler(query_params)
        access = ["aws_compute1"]
        filt = {"field": "nodes", "operation": "contained_by"}
        filters = QueryFilterCollection()
        handler.set_access_filters(access, filt, filters)
        expected = [QueryFilter(field="nodes", operation="contained_by", parameter=["aws_compute1"])]
        self.assertEqual(filters._filters, expected)

    def test_set_access_filters_with_list(self):
        """Test that the execute query runs properly with value query and an RBAC restriction on cluster."""
        key = "app"
        value = "b"
        url = f"/app/?filter[value]={value}"
        query_params = self.mocked_query_params(url, OCPTagView)
        # the mocked query parameters dont include the key from the url so it needs to be added
        query_params.kwargs = {"key": key}
        handler = OCPTagQueryHandler(query_params)
        access = ["my-ocp-cluster-2"]
        filt = [
            {"field": "report_period__cluster_id", "operation": "icontains", "composition_key": "cluster_filter"},
            {"field": "report_period__cluster_alias", "operation": "icontains", "composition_key": "cluster_filter"},
        ]
        filters = QueryFilterCollection()
        handler.set_access_filters(access, filt, filters)
        expected = []
        expected.append(
            QueryFilter(field="report_period__cluster_id", operation="icontains", parameter=["my-ocp-cluster-2"])
        )
        expected.append(
            QueryFilter(field="report_period__cluster_alias", operation="icontains", parameter=["my-ocp-cluster-2"])
        )
        self.assertEqual(filters._filters, expected)

    def test_category_filter(self):
        """Test that we can filter by category on the tags endpoint."""
        categories = {
            "Platform": [
                {"enabled": True, "key": "app", "values": ["mobile", "temperature"]},
                {"enabled": True, "key": "storageclass", "values": ["Ruby"]},
            ],
            "FAKE_CATEGORY": [],
            "*": [],
        }
        for category in categories.keys():
            with self.subTest(category=category):
                with tenant_context(self.tenant):
                    query_params = self.mocked_query_params(f"?filter[category]={category}", OCPTagView)
                    handler = OCPTagQueryHandler(query_params)
                    result_value = handler.execute_query().get("data")
                    if category == "*":
                        self.assertNotEqual(result_value, categories[category])
                    else:
                        self.assertEqual(result_value, categories[category])


class OCPTagValuesArrayFilterTest(IamTestCase):
    """Value filtering over catalog rows whose arrays span several clusters (#867).

    Once ``reporting_ocptags_values`` names every cluster that saw a key/value
    pair, "the value is used by cluster X" means "X is *a member* of
    ``cluster_ids``", not "``cluster_ids`` is contained by {X}". These tests pin
    the member semantics on the value endpoint for each user filter, for RBAC,
    and for the wildcard forms that used to bind a string to an array.
    """

    KEY = "app867"
    SHARED = "zq867-shared"
    ONLY_B = "zq867-only-b"
    QUERY = "zq867"

    def setUp(self):
        """Seed one shared value and one value only the second cluster saw."""
        super().setUp()
        with tenant_context(self.tenant):
            OCPTagsValues.objects.create(
                key=self.KEY,
                value=self.SHARED,
                cluster_ids=["c867-a", "c867-b"],
                cluster_aliases=["a867-alias", "b867-alias"],
                namespaces=["ns-867-a", "ns-867-b"],
                nodes=["node-867-a", "node-867-b"],
            )
            OCPTagsValues.objects.create(
                key=self.KEY,
                value=self.ONLY_B,
                cluster_ids=["c867-b"],
                cluster_aliases=["b867-alias"],
                namespaces=["ns-867-b"],
                nodes=["node-867-b"],
            )

    def tearDown(self):
        """Remove the seeded catalog rows."""
        with tenant_context(self.tenant):
            OCPTagsValues.objects.filter(key=self.KEY).delete()
        super().tearDown()

    def _values(self, extra_filter="", access=None):
        """Return the value endpoint's values for the test key and query token.

        ``access`` goes through ``mocked_query_params`` because the mocked
        request bypasses the identity middleware: the ``RbacPermissions``
        decorator alone leaves ``QueryParameters.access`` empty, so a decorated
        test would assert the unfiltered result.
        """
        url = f"/{self.KEY}/?filter[value]={self.QUERY}{extra_filter}"
        query_params = self.mocked_query_params(url, OCPTagView, access=access)
        query_params.kwargs = {"key": self.KEY}
        handler = OCPTagQueryHandler(query_params)
        data = handler.get_tag_values()
        if not data:
            return []
        return data[0].get("values", [])

    def test_value_matches_when_only_one_of_its_clusters_is_requested(self):
        """A value shared by two clusters must match a filter naming either one."""
        self.assertEqual([self.SHARED], self._values("&filter[cluster]=c867-a"))
        self.assertEqual(sorted([self.SHARED, self.ONLY_B]), sorted(self._values("&filter[cluster]=c867-b")))

    def test_value_matches_on_cluster_alias(self):
        """The alias half of the cluster filter uses the same member semantics."""
        self.assertEqual(sorted([self.SHARED, self.ONLY_B]), sorted(self._values("&filter[cluster]=b867-alias")))

    def test_value_matches_on_project(self):
        """A value is listed for a namespace any of its clusters used it in."""
        self.assertEqual([self.SHARED], self._values("&filter[project]=ns-867-a"))
        self.assertEqual(sorted([self.SHARED, self.ONLY_B]), sorted(self._values("&filter[project]=ns-867-b")))

    def test_rbac_node_keeps_a_value_shared_with_an_ungranted_node(self):
        """``filter[node]`` is RBAC-only, and it uses the same member semantics."""
        access = {"openshift.node": {"read": ["node-867-a"]}}
        self.assertEqual([self.SHARED], self._values(access=access))

    def test_value_not_seen_in_the_requested_cluster_is_excluded(self):
        """Member semantics must not degrade into match-everything."""
        self.assertEqual([], self._values("&filter[cluster]=c867-unknown"))

    def test_rbac_cluster_keeps_a_value_shared_with_an_ungranted_cluster(self):
        """A grant on one cluster must not hide a value another cluster also saw."""
        access = {"openshift.cluster": {"read": ["c867-b"]}}
        self.assertEqual(sorted([self.SHARED, self.ONLY_B]), sorted(self._values(access=access)))

    def test_rbac_cluster_excludes_values_of_ungranted_clusters(self):
        """RBAC must still hide a value no granted cluster saw."""
        access = {"openshift.cluster": {"read": ["c867-unknown"]}}
        self.assertEqual([], self._values(access=access))

    def test_wildcard_cluster_and_project_return_without_error(self):
        """``*`` on an array filter adds no predicate instead of binding a string."""
        expected = sorted([self.SHARED, self.ONLY_B])
        self.assertEqual(expected, sorted(self._values("&filter[cluster]=*")))
        self.assertEqual(expected, sorted(self._values("&filter[project]=*")))

    def test_and_operator_cluster_filter_binds_a_list(self):
        """The ``and:`` operator path must bind an array for the same fields."""
        self.assertEqual([self.SHARED], self._values("&filter[and:cluster]=c867-a"))
