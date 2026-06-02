#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ROS savings recalculation notification."""
from unittest.mock import patch

from django.test import override_settings

from masu.processor.ros_savings_recalc import DEFAULT_RECOMMENDATION_TYPES
from masu.processor.ros_savings_recalc import notify_ros_savings_recalculation
from masu.test import MasuTestCase


class RosSavingsRecalcTest(MasuTestCase):
    def test_default_recommendation_types_include_quota(self):
        self.assertEqual(
            ("container", "node", "pvc", "quota", "cluster-quota"),
            DEFAULT_RECOMMENDATION_TYPES,
        )

    @override_settings(ROS_OCP_BACKEND_URL="http://ros-api:8000", ROS_SERVICE_TOKEN="test-token")
    @patch("masu.processor.ros_savings_recalc.requests.post")
    def test_notify_includes_quota_types(self, mock_post):
        mock_post.return_value.status_code = 202

        notify_ros_savings_recalculation(self.schema, provider_uuid=self.ocp_provider_uuid)

        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(
            ["container", "node", "pvc", "quota", "cluster-quota"],
            payload["recommendation_types"],
        )
        self.assertEqual("1234567", payload["org_id"])
        self.assertEqual(str(self.ocp_provider_uuid), payload["cluster_uuid"])

    @patch("masu.processor.ros_savings_recalc.requests.post")
    def test_notify_noops_when_ros_not_configured(self, mock_post):
        notify_ros_savings_recalculation(self.schema)
        mock_post.assert_not_called()
