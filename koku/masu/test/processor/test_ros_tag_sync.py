#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ROS OCP tag sync."""
from unittest.mock import patch

from django.test import override_settings
from django_tenants.utils import schema_context

from api.iam.models import Tenant
from api.provider.models import Provider
from api.utils import DateHelper
from masu.processor.ros_tag_sync import build_namespace_tags_payload
from masu.processor.ros_tag_sync import org_id_from_schema
from masu.processor.ros_tag_sync import sync_ros_ocp_tags
from masu.processor.ros_tag_sync import sync_ros_ocp_tags_periodic
from masu.test import MasuTestCase
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.ocp.models import OCPUsageLineItemDailySummary


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


@override_settings(ROS_TAGS_ENABLED=True, ROS_TAGS_DEV_TOKEN="test-token", ROS_OCP_BACKEND_URL="http://ros-api:8000")
class RosTagSyncTest(MasuTestCase):
    def setUp(self):
        super().setUp()
        with schema_context(self.schema):
            EnabledTagKeys.objects.filter(provider_type=Provider.PROVIDER_OCP).delete()
            EnabledTagKeys.objects.create(key="environment", provider_type=Provider.PROVIDER_OCP, enabled=True)
            EnabledTagKeys.objects.create(key="team", provider_type=Provider.PROVIDER_OCP, enabled=False)

    def test_org_id_from_schema(self):
        self.assertEqual("1234567", org_id_from_schema("org1234567"))

    def test_build_namespace_tags_payload_filters_enabled_keys(self):
        dh = DateHelper()
        with schema_context(self.schema):
            OCPUsageLineItemDailySummary.objects.filter(
                cluster_id=self.ocp_cluster_id, namespace="ros-tag-sync-ns"
            ).delete()
            OCPUsageLineItemDailySummary.objects.create(
                uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                cluster_id=self.ocp_cluster_id,
                namespace="ros-tag-sync-ns",
                usage_start=dh.today,
                usage_end=dh.today,
                all_labels={"environment": "production", "team": "payments", "app": "api"},
            )

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
        dh = DateHelper()
        with schema_context(self.schema):
            EnabledTagKeys.objects.create(key="cost_center", provider_type=Provider.PROVIDER_OCP, enabled=True)
            OCPUsageLineItemDailySummary.objects.filter(
                cluster_id=self.ocp_cluster_id, namespace="ros-tag-sync-empty-ns"
            ).delete()
            OCPUsageLineItemDailySummary.objects.create(
                uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                cluster_id=self.ocp_cluster_id,
                namespace="ros-tag-sync-empty-ns",
                usage_start=dh.today,
                usage_end=dh.today,
                all_labels={"environment": "production"},
            )

        payload = build_namespace_tags_payload(self.schema)
        tag_keys = {entry["key"]: entry["values"] for entry in payload["tag_keys"]}
        self.assertEqual(["production"], tag_keys["environment"])
        self.assertEqual([], tag_keys["cost_center"])

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
