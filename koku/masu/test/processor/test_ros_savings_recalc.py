#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ROS savings recalculation notification."""
import os
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

    @override_settings(
        ROS_OCP_BACKEND_URL="http://ros-api:8000",
        ROS_API_HOST="",
        ROS_SERVICE_TOKEN="test-token",
    )
    @patch("masu.processor.ros_savings_recalc.requests.post")
    def test_notify_includes_quota_types(self, mock_post):
        mock_post.return_value.status_code = 202

        notify_ros_savings_recalculation(
            self.schema,
            provider_uuid=self.ocp_provider_uuid,
            cluster_id=self.ocp_cluster_id,
        )

        mock_post.assert_called_once()
        # The on-prem wiring: ROS_OCP_BACKEND_URL is the target.
        url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        self.assertIn("ros-api:8000", str(url))
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(
            ["container", "node", "pvc", "quota", "cluster-quota"],
            payload["recommendation_types"],
        )
        self.assertEqual("1234567", payload["org_id"])
        # #769 (V15-19): the payload's cluster_uuid is the provider's ACTUAL
        # OpenShift cluster ID — never the provider UUID (the consumer uses it
        # verbatim for rate-cache invalidation and recommendation selection).
        self.assertEqual(self.ocp_cluster_id, payload["cluster_uuid"])
        self.assertNotEqual(str(self.ocp_provider_uuid), payload["cluster_uuid"])

    @override_settings(ROS_API_HOST="ros-api-saas", ROS_API_PORT="8000", ROS_SERVICE_TOKEN="test-token")
    @patch("masu.processor.ros_savings_recalc.requests.post")
    def test_notify_saas_topology_same_payload(self, mock_post):
        """#769: the SaaS-style wiring (ROS_API_HOST/PORT) sends the SAME
        correct payload — the topology only changes the URL, never the
        cluster identity."""
        mock_post.return_value.status_code = 202

        notify_ros_savings_recalculation(
            self.schema,
            provider_uuid=self.ocp_provider_uuid,
            cluster_id=self.ocp_cluster_id,
        )

        mock_post.assert_called_once()
        url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args.kwargs.get("url")
        self.assertIn("ros-api-saas", str(url), "the SaaS wiring must target ROS_API_HOST")
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(
            self.ocp_cluster_id, payload["cluster_uuid"], "both topologies must send the real cluster ID (#769)"
        )

    @override_settings(ROS_OCP_BACKEND_URL="http://ros-api:8000", ROS_SERVICE_TOKEN="test-token")
    @patch("masu.processor.ros_savings_recalc.requests.post")
    def test_notify_without_cluster_id_omits_field(self, mock_post):
        """A provider whose cluster ID cannot be resolved omits cluster_uuid —
        ROS's org-wide recalculation path covers the target cluster (the
        correct degradation, never a wrong-cluster payload)."""
        mock_post.return_value.status_code = 202

        notify_ros_savings_recalculation(self.schema, provider_uuid=self.ocp_provider_uuid)

        payload = mock_post.call_args.kwargs["json"]
        self.assertNotIn(
            "cluster_uuid",
            payload,
            "an unresolvable cluster ID must omit the field, not send the provider UUID (#769)",
        )

    @patch("masu.processor.ros_savings_recalc.requests.post")
    def test_notify_noops_when_ros_not_configured(self, mock_post):
        with override_settings(ROS_OCP_BACKEND_URL="", ROS_API_HOST=""), patch.dict(
            os.environ, {"ROS_OCP_BACKEND_URL": "", "ROS_API_HOST": ""}
        ):
            notify_ros_savings_recalculation(self.schema)
        mock_post.assert_not_called()
