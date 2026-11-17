#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Regression tests for the reporting_ocptags_values array rebuild (#867).

The catalog arrays are a projection of the label summaries inside the reporting
window (`[last_month_start, this_month_end]`, ADR-0353), not a snapshot of the
report period that happened to summarize last. Two consequences are tested here:

* a key/value shared by two clusters must name both of them no matter which
  cluster summarized last, and the pod and volume runs must not replace each
  other's namespaces;
* a cluster whose report period left the window must disappear from the arrays,
  and a value with no cluster left inside the window is dropped from the catalog
  instead of keeping an empty array (an empty array satisfies every
  ``contained_by`` filter).
"""
import uuid as uuid_lib

from dateutil.relativedelta import relativedelta
from django_tenants.utils import schema_context

from api.provider.models import Provider
from api.utils import DateHelper
from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
from masu.test import MasuTestCase
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.ocp.models import OCPStorageVolumeLabelSummary
from reporting.provider.ocp.models import OCPTagsValues
from reporting.provider.ocp.models import OCPUsageLineItemDailySummary
from reporting.provider.ocp.models import OCPUsagePodLabelSummary
from reporting.provider.ocp.models import OCPUsageReportPeriod

TAG_KEY = "probe"


class OCPTagValuesArrayRebuildTest(MasuTestCase):
    """Test the tag catalog arrays are rebuilt from the whole window."""

    def setUp(self):
        """Isolate the label tables so each assertion sees only this test's rows."""
        super().setUp()
        self.dh = DateHelper()
        self.run_start = self.dh.this_month_start.date()
        self.run_end = self.dh.this_month_end.date()
        with schema_context(self.schema):
            OCPTagsValues.objects.all().delete()
            OCPUsagePodLabelSummary.objects.all().delete()
            OCPStorageVolumeLabelSummary.objects.all().delete()
            OCPUsageLineItemDailySummary.objects.all().delete()
            EnabledTagKeys.objects.filter(provider_type=Provider.PROVIDER_OCP).delete()
            EnabledTagKeys.objects.create(key=TAG_KEY, provider_type=Provider.PROVIDER_OCP, enabled=True)

    def _period(self, cluster_id, period_start=None):
        """Create a report period for a cluster."""
        period_start = period_start or self.dh.this_month_start
        with schema_context(self.schema):
            return OCPUsageReportPeriod.objects.create(
                cluster_id=cluster_id,
                cluster_alias=f"{cluster_id}-alias",
                report_period_start=period_start,
                report_period_end=period_start,
                provider_id=self.ocp_provider_uuid,
            )

    def _daily_row(self, period, namespace, node, value, data_source="Pod"):
        """Seed the usage fact table the label summary is built from."""
        labels = {"pod_labels": {TAG_KEY: value}}
        if data_source == "Storage":
            labels = {"volume_labels": {TAG_KEY: value}}
        with schema_context(self.schema):
            OCPUsageLineItemDailySummary.objects.create(
                uuid=uuid_lib.uuid4(),
                report_period=period,
                cluster_id=period.cluster_id,
                cluster_alias=period.cluster_alias,
                data_source=data_source,
                namespace=namespace,
                node=node,
                usage_start=period.report_period_start.date(),
                usage_end=period.report_period_start.date(),
                **labels,
            )

    def _label_summary(self, period, namespace, node, value, model=OCPUsagePodLabelSummary):
        """Seed a label summary row directly (for periods no run touches here)."""
        with schema_context(self.schema):
            model.objects.create(key=TAG_KEY, values=[value], report_period=period, namespace=namespace, node=node)

    def _run(self, method, periods, start=None, end=None):
        """Run one label summary builder for the given periods.

        No ``window_start``/``window_end`` is passed on purpose: the tests must
        pin the production default window (``[last_month_start, this_month_end]``,
        ADR-0353) rather than whatever a test chooses.
        """
        with OCPReportDBAccessor(schema=self.schema) as accessor:
            getattr(accessor, method)(
                [period.id for period in periods],
                start or self.run_start,
                end or self.run_end,
            )

    def _catalog(self, value):
        """Return the catalog row for the test key and a value."""
        with schema_context(self.schema):
            return OCPTagsValues.objects.get(key=TAG_KEY, value=value)

    def test_arrays_accumulate_across_clusters(self):
        """A shared value names every cluster once both have summarized."""
        period_a = self._period("cluster-a")
        period_b = self._period("cluster-b")
        self._daily_row(period_a, "ns-a", "node-a", "shared")
        self._daily_row(period_b, "ns-b", "node-b", "shared")

        self._run("populate_pod_label_summary_table", [period_a])
        self.assertEqual(["cluster-a"], self._catalog("shared").cluster_ids)

        self._run("populate_pod_label_summary_table", [period_b])
        row = self._catalog("shared")
        self.assertEqual(["cluster-a", "cluster-b"], row.cluster_ids)
        self.assertEqual(["cluster-a-alias", "cluster-b-alias"], row.cluster_aliases)
        self.assertEqual(["ns-a", "ns-b"], row.namespaces)
        self.assertEqual(["node-a", "node-b"], row.nodes)

        # Order-independent: one run covering both periods, listed backwards,
        # must land on the same arrays.
        self._run("populate_pod_label_summary_table", [period_b, period_a])
        self.assertEqual(["cluster-a", "cluster-b"], self._catalog("shared").cluster_ids)

    def test_resummarizing_one_cluster_keeps_the_other(self):
        """Re-running the first cluster must not take the shared value back."""
        period_a = self._period("cluster-a")
        period_b = self._period("cluster-b")
        self._daily_row(period_a, "ns-a", "node-a", "shared")
        self._daily_row(period_b, "ns-b", "node-b", "shared")
        self._run("populate_pod_label_summary_table", [period_a])
        self._run("populate_pod_label_summary_table", [period_b])

        self._run("populate_pod_label_summary_table", [period_a])
        self.assertEqual(["cluster-a", "cluster-b"], self._catalog("shared").cluster_ids)

    def test_storage_labels_add_namespaces_to_pod_labels(self):
        """The volume run must add its namespaces, not replace the pod ones."""
        period_a = self._period("cluster-a")
        period_b = self._period("cluster-b")
        self._daily_row(period_a, "ns-a", "node-a", "shared")
        self._daily_row(period_b, "ns-b", "node-b", "shared")
        self._daily_row(period_a, "ns-storage", "node-a", "shared", data_source="Storage")
        self._run("populate_pod_label_summary_table", [period_a])
        self._run("populate_pod_label_summary_table", [period_b])

        self._run("populate_volume_label_summary_table", [period_a])
        row = self._catalog("shared")
        self.assertEqual(["cluster-a", "cluster-b"], row.cluster_ids)
        self.assertEqual(["ns-a", "ns-b", "ns-storage"], row.namespaces)

    def test_cluster_outside_the_window_is_excluded(self):
        """Only periods starting inside [last_month_start, this_month_end] contribute."""
        period_a = self._period("cluster-a")
        period_e = self._period("cluster-e", self.dh.last_month_start)
        period_old = self._period("cluster-old", self.dh.this_month_start - relativedelta(months=2))
        self._daily_row(period_a, "ns-a", "node-a", "shared")
        # Both older periods keep their label-summary history; only cluster-e is
        # still inside the window.
        self._label_summary(period_e, "ns-e", "node-e", "shared")
        self._label_summary(period_old, "ns-old", "node-old", "shared")

        self._run("populate_pod_label_summary_table", [period_a])
        row = self._catalog("shared")
        self.assertEqual(["cluster-a", "cluster-e"], row.cluster_ids)
        self.assertEqual(["ns-a", "ns-e"], row.namespaces)

    def test_value_without_cluster_inside_the_window_is_deleted(self):
        """A republished period outside the window drops its value, keeping history."""
        old_start = self.dh.this_month_start - relativedelta(months=2)
        period_c = self._period("cluster-c", old_start)
        self._daily_row(period_c, "ns-c", "node-c", "gone")
        with schema_context(self.schema):
            OCPTagsValues.objects.create(
                key=TAG_KEY,
                value="gone",
                cluster_ids=["cluster-c"],
                cluster_aliases=["cluster-c-alias"],
                namespaces=["ns-c"],
                nodes=["node-c"],
            )

        self._run(
            "populate_pod_label_summary_table",
            [period_c],
            start=old_start.date(),
            end=(old_start + relativedelta(months=1) - relativedelta(days=1)).date(),
        )
        with schema_context(self.schema):
            # An empty array would still satisfy every contained_by filter, so the
            # row goes instead of being emptied.
            self.assertFalse(OCPTagsValues.objects.filter(key=TAG_KEY, value="gone").exists())
            # The label summary itself is history and stays; only the catalog row goes.
            self.assertTrue(OCPUsagePodLabelSummary.objects.filter(report_period=period_c, key=TAG_KEY).exists())
