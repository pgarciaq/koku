#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ROS OCP tag sync."""
import os
import tempfile
import uuid as uuid_lib
from unittest.mock import patch

from django.test import override_settings
from django.test import SimpleTestCase
from django_tenants.utils import schema_context

from api.iam.models import Tenant
from api.provider.models import Provider
from api.utils import DateHelper
from masu.processor.ros_tag_sync import _read_bearer_token
from masu.processor.ros_tag_sync import build_namespace_tags_payload
from masu.processor.ros_tag_sync import org_id_from_schema
from masu.processor.ros_tag_sync import sync_ros_ocp_tags
from masu.processor.ros_tag_sync import sync_ros_ocp_tags_periodic
from masu.test import MasuTestCase
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.ocp.models import OCPStorageVolumeLabelSummary
from reporting.provider.ocp.models import OCPUsageLineItemDailySummary
from reporting.provider.ocp.models import OCPUsagePodLabelSummary
from reporting.provider.ocp.models import OCPUsageReportPeriod


@override_settings(ROS_TAGS_ENABLED=False)
class RosTagSyncDisabledTest(MasuTestCase):
    @patch("masu.processor.ros_tag_sync.push_namespace_tags")
    def test_sync_noops_when_disabled(self, mock_push):
        sync_ros_ocp_tags(self.schema)
        mock_push.assert_not_called()

    @patch("masu.processor.ros_tag_sync.sync_ros_ocp_tags.delay")
    def test_periodic_sync_noops_when_disabled(self, mock_delay):
        sync_ros_ocp_tags_periodic()
        mock_delay.assert_not_called()


@override_settings(ROS_TAGS_ENABLED=True, ROS_TAGS_SOURCE="db")
class RosTagSyncDBSourceTest(MasuTestCase):
    @patch("masu.processor.ros_tag_sync.push_namespace_tags")
    def test_sync_noops_when_db_source(self, mock_push):
        sync_ros_ocp_tags(self.schema)
        mock_push.assert_not_called()

    @patch("masu.processor.ros_tag_sync.sync_ros_ocp_tags.delay")
    def test_periodic_sync_noops_when_db_source(self, mock_delay):
        sync_ros_ocp_tags_periodic()
        mock_delay.assert_not_called()


@override_settings(
    ROS_TAGS_ENABLED=True,
    ROS_TAGS_SOURCE="api",
    ROS_TAGS_DEV_TOKEN="test-token",
    ROS_OCP_BACKEND_URL="http://ros-api:8000",
)
class RosTagSyncTest(MasuTestCase):
    def setUp(self):
        super().setUp()
        with schema_context(self.schema):
            # KokuTestRunner seeds this tenant with many OCP namespaces; these
            # payload tests need to observe only the rows each test creates.
            OCPUsageLineItemDailySummary.objects.all().delete()
            OCPUsagePodLabelSummary.objects.all().delete()
            OCPStorageVolumeLabelSummary.objects.all().delete()
            EnabledTagKeys.objects.filter(provider_type=Provider.PROVIDER_OCP).delete()
            EnabledTagKeys.objects.create(key="environment", provider_type=Provider.PROVIDER_OCP, enabled=True)
            EnabledTagKeys.objects.create(key="team", provider_type=Provider.PROVIDER_OCP, enabled=False)

    def _report_period(self, cluster_id, period_start=None):
        """Return a report period in the tags-API window, creating it if needed."""
        dh = DateHelper()
        period, _ = OCPUsageReportPeriod.objects.get_or_create(
            cluster_id=cluster_id,
            report_period_start=period_start or dh.this_month_start,
            provider_id=self.ocp_provider_uuid,
            defaults={"report_period_end": dh.this_month_end},
        )
        return period

    def _label_summary(self, model, cluster_id, namespace, key, values, period_start=None, node="ros-tag-node"):
        """Seed a label summary row, the source Koku's tags API reads."""
        model.objects.update_or_create(
            key=key,
            report_period=self._report_period(cluster_id, period_start),
            namespace=namespace,
            node=node,
            defaults={"values": values},
        )

    def test_org_id_from_schema(self):
        self.assertEqual("1234567", org_id_from_schema("org1234567"))

    def test_build_namespace_tags_payload_filters_enabled_keys(self):
        with schema_context(self.schema):
            for key, values in (
                ("environment", ["production"]),
                ("team", ["payments"]),
                ("app", ["api"]),
            ):
                self._label_summary(OCPUsagePodLabelSummary, self.ocp_cluster_id, "ros-tag-sync-ns", key, values)

        payload = build_namespace_tags_payload(self.schema)
        self.assertEqual(org_id_from_schema(self.schema), payload["org_id"])
        self.assertIn("synced_at", payload)
        self.assertTrue(payload["synced_at"].endswith("Z"))
        self.assertEqual(1, len(payload["namespace_tags"]))
        entry = payload["namespace_tags"][0]
        self.assertEqual(self.ocp_cluster_id, entry["cluster_uuid"])
        self.assertEqual("ros-tag-sync-ns", entry["namespace"])
        self.assertEqual({"environment": "production"}, entry["tags"])
        self.assertEqual(
            [{"key": "environment", "values": ["production"]}],
            payload["tag_keys"],
        )

    def test_build_namespace_tags_payload_includes_empty_values_for_enabled_key(self):
        with schema_context(self.schema):
            EnabledTagKeys.objects.create(key="cost_center", provider_type=Provider.PROVIDER_OCP, enabled=True)
            self._label_summary(
                OCPUsagePodLabelSummary, self.ocp_cluster_id, "ros-tag-sync-empty-ns", "environment", ["production"]
            )

        payload = build_namespace_tags_payload(self.schema)
        tag_keys = {entry["key"]: entry["values"] for entry in payload["tag_keys"]}
        self.assertEqual(["production"], tag_keys["environment"])
        self.assertEqual([], tag_keys["cost_center"])

    def test_build_namespace_tags_payload_spans_clusters_and_report_periods(self):
        """A cluster whose last report is older must not lose its tags (#770)."""
        dh = DateHelper()
        cluster_a, cluster_b = "ros-tag-cluster-a", "ros-tag-cluster-b"
        with schema_context(self.schema):
            EnabledTagKeys.objects.create(key="app", provider_type=Provider.PROVIDER_OCP, enabled=True)
            period_a = OCPUsageReportPeriod.objects.create(
                cluster_id=cluster_a,
                cluster_alias="ros tag cluster a",
                report_period_start=dh.last_month_start,
                report_period_end=dh.last_month_end,
                provider_id=self.ocp_provider_uuid,
            )
            period_b = OCPUsageReportPeriod.objects.create(
                cluster_id=cluster_b,
                cluster_alias="ros tag cluster b",
                report_period_start=dh.this_month_start,
                report_period_end=dh.this_month_end,
                provider_id=self.ocp_provider_uuid,
            )
            # Both sources are seeded on purpose: the fact table is what the
            # pre-fix builder reads, the label summaries are what Koku's tags API
            # reads. Only the label summaries can answer for both clusters.
            for cluster_id, namespace, usage_start in (
                (cluster_a, "ros-tag-ns-a", dh.last_month_start),
                (cluster_b, "ros-tag-ns-b", dh.today),
            ):
                OCPUsageLineItemDailySummary.objects.create(
                    uuid=uuid_lib.uuid4(),
                    cluster_id=cluster_id,
                    namespace=namespace,
                    usage_start=usage_start,
                    usage_end=usage_start,
                    all_labels={"environment": "production"},
                )
            OCPUsagePodLabelSummary.objects.create(
                key="environment",
                values=["production"],
                report_period=period_a,
                namespace="ros-tag-ns-a",
                node="ros-tag-node-a",
            )
            OCPUsagePodLabelSummary.objects.create(
                key="environment",
                values=["production"],
                report_period=period_b,
                namespace="ros-tag-ns-b",
                node="ros-tag-node-b",
            )
            # Storage labels are a second source in Koku's tags API; a payload
            # built from pod labels alone would silently drop them.
            self._label_summary(
                OCPStorageVolumeLabelSummary,
                cluster_a,
                "ros-tag-ns-a",
                "app",
                ["cost-management"],
                period_start=dh.last_month_start,
            )

        payload = build_namespace_tags_payload(self.schema)
        got = {(entry["cluster_uuid"], entry["namespace"]) for entry in payload["namespace_tags"]}
        self.assertEqual(
            {(cluster_a, "ros-tag-ns-a"), (cluster_b, "ros-tag-ns-b")},
            got,
            "each cluster's namespaces appear regardless of its summarize day",
        )
        tags_by_namespace = {
            (entry["cluster_uuid"], entry["namespace"]): entry["tags"] for entry in payload["namespace_tags"]
        }
        self.assertEqual(
            {"environment": "production", "app": "cost-management"},
            tags_by_namespace[(cluster_a, "ros-tag-ns-a")],
        )
        self.assertEqual(
            {"environment": "production"},
            tags_by_namespace[(cluster_b, "ros-tag-ns-b")],
        )
        self.assertEqual(
            [
                {"key": "app", "values": ["cost-management"]},
                {"key": "environment", "values": ["production"]},
            ],
            payload["tag_keys"],
        )

    def test_build_namespace_tags_payload_collapses_multi_value_deterministically(self):
        """Koku returns a value list per key; the payload must pick one, reproducibly."""
        with schema_context(self.schema):
            self._label_summary(
                OCPUsagePodLabelSummary,
                self.ocp_cluster_id,
                "ros-tag-multi-ns",
                "environment",
                ["staging"],
                node="ros-tag-node-z",
            )
            self._label_summary(
                OCPUsagePodLabelSummary,
                self.ocp_cluster_id,
                "ros-tag-multi-ns",
                "environment",
                ["production"],
                node="ros-tag-node-a",
            )

        payload = build_namespace_tags_payload(self.schema)
        self.assertEqual(1, len(payload["namespace_tags"]))
        self.assertEqual({"environment": "production"}, payload["namespace_tags"][0]["tags"])
        # The catalog is the org-wide union; resolved_tags holds a single value
        # until multi-value storage exists (#814).
        self.assertEqual(
            [{"key": "environment", "values": ["production", "staging"]}],
            payload["tag_keys"],
        )

    @patch("masu.processor.ros_tag_sync.push_namespace_tags", return_value=2)
    @patch(
        "masu.processor.ros_tag_sync.build_namespace_tags_payload",
        return_value={
            "org_id": "1234567",
            "synced_at": "2026-05-25T12:00:00Z",
            "tag_keys": [],
            "namespace_tags": [],
        },
    )
    def test_sync_posts_payload(self, mock_build, mock_push):
        sync_ros_ocp_tags(self.schema, tracing_id="trace-1")
        mock_build.assert_called_once_with(self.schema)
        mock_push.assert_called_once()

    @patch("masu.processor.ros_tag_sync.sync_ros_ocp_tags.delay")
    def test_periodic_sync_queues_all_tenants(self, mock_delay):
        tenant_count = Tenant.objects.exclude(schema_name="public").count()
        sync_ros_ocp_tags_periodic(tracing_id="trace-periodic")
        self.assertEqual(tenant_count, mock_delay.call_count)


@override_settings(ROS_TAGS_DEV_TOKEN="")
class RosBearerTokenPathTest(SimpleTestCase):
    def test_read_bearer_token_prefers_ros_sa_token_path_env(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as token_file:
            token_file.write("projected-token-content\n")
            token_path = token_file.name

        try:
            with patch.dict(os.environ, {"ROS_SA_TOKEN_PATH": token_path}, clear=False):
                self.assertEqual("projected-token-content", _read_bearer_token())
        finally:
            os.unlink(token_path)
