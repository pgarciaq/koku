#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
import json
from unittest.mock import MagicMock
from unittest.mock import patch

from django.test import TestCase

from masu.external.ros_report_shipper import ROSReportShipper
from masu.test.util.ocp.test_common import ManifestFactory
from masu.util.ocp import common as utils


class TestROSReportShipperCustomTimeframes(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manifest = ManifestFactory.build(manifest_id=400, cluster_id="test-cluster")
        payload = utils.PayloadInfo(
            request_id="req-1",
            manifest=cls.manifest,
            source_id="1",
            provider_uuid="12345678-1234-4234-a234-123456789abc",
            provider_type="OCP",
            cluster_alias="test-alias",
            account_id="10001",
            org_id="1234567",
            schema_name="org1234567",
            trino_schema="org1234567",
        )
        with patch("masu.external.ros_report_shipper.get_ros_s3_client"):
            cls.shipper = ROSReportShipper(payload, "b64identity", {"account": "10001", "org_id": "1234567"})

    def test_build_ros_msg_includes_custom_timeframes(self):
        """build_ros_msg embeds custom_timeframes from tenant settings in metadata."""
        mock_settings = {
            "terms": [{"name": "term1", "duration_days": 3}],
            "business_hours": {"enabled": False},
        }
        with patch.object(self.shipper, "_get_ros_custom_timeframes", return_value=mock_settings):
            msg = self.shipper.build_ros_msg(["https://url1"], ["key1"])
        parsed = json.loads(msg)
        self.assertIn("custom_timeframes", parsed["metadata"])
        self.assertEqual(parsed["metadata"]["custom_timeframes"]["terms"][0]["duration_days"], 3)

    @patch("masu.external.ros_report_shipper.get_producer")
    def test_send_kafka_message_uses_org_id_key(self, mock_get_producer):
        """send_kafka_message passes org_id as the Kafka message key."""
        mock_producer = MagicMock()
        mock_get_producer.return_value = mock_producer

        self.shipper.send_kafka_message(b'{"test": true}')

        mock_producer.produce.assert_called_once()
        call_kwargs = mock_producer.produce.call_args
        self.assertEqual(call_kwargs.kwargs.get("key") or call_kwargs[1].get("key"), b"1234567")

    def test_build_ros_msg_null_custom_timeframes_when_no_settings(self):
        """When no custom settings configured, custom_timeframes is None/default."""
        with patch.object(self.shipper, "_get_ros_custom_timeframes", return_value=None):
            msg = self.shipper.build_ros_msg(["https://url1"], ["key1"])
        parsed = json.loads(msg)
        self.assertIsNone(parsed["metadata"].get("custom_timeframes"))

    @patch("masu.external.ros_report_shipper.schema_context")
    def test_get_ros_custom_timeframes_uses_schema_context(self, mock_schema_ctx):
        """_get_ros_custom_timeframes queries settings within the tenant schema."""
        mock_schema_ctx.return_value.__enter__ = MagicMock()
        mock_schema_ctx.return_value.__exit__ = MagicMock()
        self.shipper._get_ros_custom_timeframes()
        mock_schema_ctx.assert_called_with("org1234567")

    def test_get_ros_custom_timeframes_db_error_returns_none(self):
        """If the DB query fails, build_ros_msg catches it and omits custom_timeframes."""
        with patch.object(self.shipper, "_get_ros_custom_timeframes", side_effect=Exception("DB error")):
            msg = self.shipper.build_ros_msg(["https://url1"], ["key1"])
        parsed = json.loads(msg)
        self.assertIsNone(parsed["metadata"].get("custom_timeframes"))

    @patch("masu.external.ros_report_shipper.get_producer")
    def test_send_kafka_message_missing_org_id(self, mock_get_producer):
        """If org_id is missing from metadata, partition key is empty bytes."""
        mock_producer = MagicMock()
        mock_get_producer.return_value = mock_producer

        shipper_no_org = self.shipper
        original_metadata = shipper_no_org.metadata.copy()
        shipper_no_org.metadata = {k: v for k, v in original_metadata.items() if k != "org_id"}
        try:
            shipper_no_org.send_kafka_message(b'{"test": true}')
            call_kwargs = mock_producer.produce.call_args
            key = call_kwargs.kwargs.get("key") or call_kwargs[1].get("key")
            self.assertEqual(key, b"")
        finally:
            shipper_no_org.metadata = original_metadata
