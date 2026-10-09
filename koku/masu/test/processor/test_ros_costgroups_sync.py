#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ROS OCP cost-groups sync (#675)."""
from unittest.mock import patch

from django.test import override_settings
from django_tenants.utils import schema_context

from api.iam.models import Tenant
from masu.processor.ros_costgroups_sync import build_costgroups_payload
from masu.processor.ros_costgroups_sync import sync_ros_ocp_costgroups
from masu.processor.ros_costgroups_sync import sync_ros_ocp_costgroups_periodic
from masu.test import MasuTestCase
from reporting.provider.ocp.models import OpenshiftCostCategory
from reporting.provider.ocp.models import OpenshiftCostCategoryNamespace

TEST_NAMESPACES = ("cg-sync-team", "cg-sync-%", "cg-sync-admin", "cg-sync-other")


@override_settings(ROS_COSTGROUPS_ENABLED=False)
class RosCostgroupsSyncDisabledTest(MasuTestCase):
    @patch("masu.processor.ros_costgroups_sync.push_costgroups")
    def test_sync_noops_when_disabled(self, mock_push):
        sync_ros_ocp_costgroups(self.schema)
        mock_push.assert_not_called()

    @patch("masu.processor.ros_costgroups_sync.sync_ros_ocp_costgroups.delay")
    def test_periodic_sync_noops_when_disabled(self, mock_delay):
        sync_ros_ocp_costgroups_periodic()
        mock_delay.assert_not_called()


@override_settings(ROS_COSTGROUPS_ENABLED=True, ROS_COSTGROUPS_SOURCE="db")
class RosCostgroupsSyncDBSourceTest(MasuTestCase):
    @patch("masu.processor.ros_costgroups_sync.push_costgroups")
    def test_sync_noops_when_db_source(self, mock_push):
        sync_ros_ocp_costgroups(self.schema)
        mock_push.assert_not_called()

    @patch("masu.processor.ros_costgroups_sync.sync_ros_ocp_costgroups.delay")
    def test_periodic_sync_noops_when_db_source(self, mock_delay):
        sync_ros_ocp_costgroups_periodic()
        mock_delay.assert_not_called()


@override_settings(
    ROS_COSTGROUPS_ENABLED=True,
    ROS_COSTGROUPS_SOURCE="api",
    ROS_TAGS_DEV_TOKEN="test-token",
    ROS_OCP_BACKEND_URL="http://ros-api:8000",
)
class RosCostgroupsSyncTest(MasuTestCase):
    def setUp(self):
        super().setUp()
        with schema_context(self.schema):
            OpenshiftCostCategoryNamespace.objects.filter(namespace__in=TEST_NAMESPACES).delete()
            platform = OpenshiftCostCategory.objects.get(name="Platform")
            other, _ = OpenshiftCostCategory.objects.get_or_create(
                name="CgSyncOther", defaults={"description": "test-only", "source_type": "OCP", "label": []}
            )
            OpenshiftCostCategoryNamespace.objects.create(
                namespace="cg-sync-team", system_default=True, cost_category=platform
            )
            OpenshiftCostCategoryNamespace.objects.create(
                namespace="cg-sync-%", system_default=True, cost_category=platform
            )
            OpenshiftCostCategoryNamespace.objects.create(
                namespace="cg-sync-admin", system_default=False, cost_category=platform
            )
            OpenshiftCostCategoryNamespace.objects.create(
                namespace="cg-sync-other", system_default=False, cost_category=other
            )
        self.addCleanup(self._delete_test_namespaces)

    def _delete_test_namespaces(self):
        with schema_context(self.schema):
            OpenshiftCostCategoryNamespace.objects.filter(namespace__in=TEST_NAMESPACES).delete()

    def test_build_payload_ships_whole_platform_group(self):
        payload = build_costgroups_payload(self.schema)
        self.assertEqual("1234567", payload["org_id"])
        self.assertTrue(payload["synced_at"].endswith("Z"))
        by_ns = {entry["namespace"]: entry for entry in payload["entries"]}
        self.assertIn("cg-sync-team", by_ns)
        self.assertIn("cg-sync-%", by_ns)
        self.assertIn("cg-sync-admin", by_ns)
        self.assertNotIn("cg-sync-other", by_ns)

    def test_build_payload_marks_provenance_and_wildcards(self):
        payload = build_costgroups_payload(self.schema)
        by_ns = {entry["namespace"]: entry for entry in payload["entries"]}
        self.assertFalse(by_ns["cg-sync-team"]["is_prefix"])
        self.assertTrue(by_ns["cg-sync-team"]["system_default"])
        # Wildcard passes through verbatim; ROS strips the suffix at read.
        self.assertEqual("cg-sync-%", by_ns["cg-sync-%"]["namespace"])
        self.assertTrue(by_ns["cg-sync-%"]["is_prefix"])
        self.assertFalse(by_ns["cg-sync-admin"]["system_default"])

    @patch("masu.processor.ros_costgroups_sync.push_costgroups", return_value=3)
    @patch(
        "masu.processor.ros_costgroups_sync.build_costgroups_payload",
        return_value={
            "org_id": "1234567",
            "synced_at": "2026-10-07T12:00:00Z",
            "entries": [],
        },
    )
    def test_sync_posts_payload(self, mock_build, mock_push):
        sync_ros_ocp_costgroups(self.schema, tracing_id="trace-1")
        mock_build.assert_called_once_with(self.schema)
        mock_push.assert_called_once()

    @patch("masu.processor.ros_costgroups_sync.sync_ros_ocp_costgroups.delay")
    def test_periodic_sync_queues_all_tenants(self, mock_delay):
        tenant_count = Tenant.objects.exclude(schema_name="public").count()
        sync_ros_ocp_costgroups_periodic(tracing_id="trace-periodic")
        self.assertEqual(tenant_count, mock_delay.call_count)
