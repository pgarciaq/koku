#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for masu reship_ros endpoint (business hours re-ingestion)."""
import json
from datetime import date
from datetime import datetime
from datetime import timezone
from http import HTTPStatus
from unittest.mock import MagicMock
from unittest.mock import patch
from uuid import uuid4

from botocore.exceptions import ClientError
from django.test import TestCase
from django.test.utils import override_settings
from model_bakery import baker
from rest_framework.test import APIRequestFactory

from masu.api.reship_ros import _filter_keys_by_manifest_metadata
from masu.api.reship_ros import reship_ros
from masu.config import Config as masu_config

ROS_S3_TEST_SETTINGS = {
    "S3_ROS_ACCESS_KEY": "test-ros-access-key",
    "S3_ROS_SECRET": "test-ros-secret",
    "S3_ROS_BUCKET_NAME": "ros-data",
}


@override_settings(**ROS_S3_TEST_SETTINGS)
class ReshipRosTests(TestCase):
    """BH-INT-020 through BH-INT-027, BH-INT-034–036, BH-INT-041."""

    @classmethod
    def setUpTestData(cls):
        cls.schema = "org1234567"
        cls.org_id = "1234567"
        cls.provider_uuid = uuid4()
        cls.cluster_id = "bh-reship-cluster"
        cls.factory = APIRequestFactory()

        auth = baker.make("ProviderAuthentication", credentials={"cluster_id": cls.cluster_id})
        cls.provider = baker.make(
            "Provider",
            uuid=cls.provider_uuid,
            name="BH Reship Cluster",
            type="OCP",
            authentication=auth,
        )
        baker.make(
            "Sources",
            source_id=99,
            koku_uuid=str(cls.provider_uuid),
            org_id=cls.org_id,
            account_id="10001",
            auth_header="test-identity",
            source_type="OCP",
            provider=cls.provider,
        )

    def _post(self, query: str = ""):
        request = self.factory.post(f"/api/cost-management/v1/reship_ros/{query}")
        return reship_ros(request)

    def _valid_query(self, start="2026-01-01", end="2026-01-03"):
        return f"?schema={self.schema}&provider_uuid={self.provider_uuid}" f"&start_date={start}&end_date={end}"

    def _mock_s3_list(self, keys_by_prefix: dict):
        """Return a mock S3 client that lists keys per Prefix."""

        def list_objects_v2(**kwargs):
            prefix = kwargs.get("Prefix", "")
            contents = [{"Key": key} for key in keys_by_prefix.get(prefix, [])]
            return {"Contents": contents, "IsTruncated": False}

        mock_client = MagicMock()
        mock_client.list_objects_v2.side_effect = list_objects_v2
        return mock_client

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.generate_s3_object_url", side_effect=lambda _c, key: f"https://s3/{key}")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_success(self, mock_s3_client, mock_presign, mock_kafka):
        """BH-INT-020: valid params publish one Kafka message per S3 object."""
        prefix = f"{self.schema}/source={self.provider_uuid}/date="
        keys_by_prefix = {
            f"{prefix}2026-01-01": ["file1.csv"],
            f"{prefix}2026-01-02": ["file2.csv"],
            f"{prefix}2026-01-03": ["file3.csv"],
        }
        mock_s3_client.return_value = self._mock_s3_list(keys_by_prefix)

        response = self._post(self._valid_query())

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.data["files_processed"], 3)
        self.assertEqual(response.data["files_total"], 3)
        self.assertEqual(mock_kafka.call_count, 3)
        for call in mock_kafka.call_args_list:
            msg = json.loads(call.args[0].decode("utf-8"))
            self.assertTrue(msg["files"][0].startswith("https://s3/"))
            self.assertEqual(len(msg["files"]), 1)
            self.assertEqual(len(msg["object_keys"]), 1)

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.generate_s3_object_url", return_value="https://s3/presigned")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_lists_s3_prefix(self, mock_s3_client, *_mocks):
        """BH-INT-020 (plan): list uses schema/source=/date= prefixes."""
        mock_client = self._mock_s3_list({})
        mock_s3_client.return_value = mock_client

        self._post(self._valid_query(start="2026-02-01", end="2026-02-02"))

        prefixes = [call.kwargs["Prefix"] for call in mock_client.list_objects_v2.call_args_list]
        self.assertEqual(
            prefixes,
            [
                f"{self.schema}/source={self.provider_uuid}/date=2026-02-01",
                f"{self.schema}/source={self.provider_uuid}/date=2026-02-02",
            ],
        )

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.generate_s3_object_url", return_value="https://s3/presigned")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_kafka_message_shape(self, mock_s3_client, _mock_presign, mock_kafka):
        """BH-INT-021: Kafka payload matches ROSReportShipper format."""
        key = f"{self.schema}/source={self.provider_uuid}/date=2026-01-01/report.csv"
        prefix = f"{self.schema}/source={self.provider_uuid}/date=2026-01-01"
        mock_s3_client.return_value = self._mock_s3_list({prefix: [key]})

        with patch("masu.api.reship_ros.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "req-abc"
            mock_uuid.return_value = MagicMock(hex="req-abc")
            self._post(self._valid_query(start="2026-01-01", end="2026-01-01"))

        msg = json.loads(mock_kafka.call_args.args[0].decode("utf-8"))
        self.assertEqual(msg["request_id"], "req-abc")
        self.assertEqual(msg["b64_identity"], "test-identity")
        self.assertEqual(msg["metadata"]["org_id"], self.org_id)
        self.assertEqual(msg["metadata"]["source_id"], "99")
        self.assertEqual(msg["metadata"]["provider_uuid"], str(self.provider_uuid))
        self.assertEqual(msg["metadata"]["cluster_uuid"], self.cluster_id)
        self.assertEqual(msg["metadata"]["cluster_alias"], "BH Reship Cluster")
        self.assertEqual(msg["files"], ["https://s3/presigned"])
        self.assertEqual(msg["object_keys"], [key])

    def test_reship_ros_missing_schema(self):
        """BH-INT-034 / BH-INT-021 (user): missing schema → 400."""
        response = self._post(f"?provider_uuid={self.provider_uuid}&start_date=2026-01-01&end_date=2026-01-02")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.data["error"], "schema is required")

    def test_reship_ros_missing_provider_uuid(self):
        """BH-INT-022: missing provider_uuid → 400."""
        response = self._post(f"?schema={self.schema}&start_date=2026-01-01&end_date=2026-01-02")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response.data["error"], "provider_uuid is required")

    def test_reship_ros_invalid_date_range(self):
        """BH-INT-035 / BH-INT-023: end before start → 400."""
        response = self._post(self._valid_query(start="2026-02-10", end="2026-02-01"))
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertIn("end_date", response.data["error"])

    def test_reship_ros_invalid_provider_uuid(self):
        """BH-INT-023 (plan): unknown provider → 400."""
        missing = uuid4()
        query = f"?schema={self.schema}&provider_uuid={missing}&start_date=2026-01-01&end_date=2026-01-02"
        response = self._post(query)
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.generate_s3_object_url", return_value="https://s3/presigned")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_no_reupload(self, mock_s3_client, mock_presign, mock_kafka):
        """BH-INT-024: never upload to S3 — list and presign only."""
        prefix = f"{self.schema}/source={self.provider_uuid}/date=2026-01-01"
        mock_client = self._mock_s3_list({prefix: [f"{prefix}/a.csv"]})
        mock_s3_client.return_value = mock_client

        self._post(self._valid_query(start="2026-01-01", end="2026-01-01"))

        mock_client.upload_fileobj.assert_not_called()
        mock_client.put_object.assert_not_called()
        mock_client.list_objects_v2.assert_called()
        mock_presign.assert_called()
        mock_kafka.assert_called_once()

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.generate_s3_object_url", return_value="https://s3/presigned")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_date_filtering(self, mock_s3_client, *_mocks):
        """BH-INT-025: only in-range dates produce Kafka messages."""
        prefix = f"{self.schema}/source={self.provider_uuid}/date="
        keys_by_prefix = {
            f"{prefix}2026-01-01": ["in-range.csv"],
            f"{prefix}2026-01-05": ["out-of-range.csv"],
        }
        mock_s3_client.return_value = self._mock_s3_list(keys_by_prefix)

        response = self._post(self._valid_query(start="2026-01-01", end="2026-01-02"))

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.data["files_processed"], 1)
        self.assertEqual(_mocks[0].call_count, 1)

    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_empty_results(self, mock_s3_client):
        """BH-INT-026 / BH-INT-025 (plan): no objects → 200 count 0."""
        mock_s3_client.return_value = self._mock_s3_list({})

        response = self._post(self._valid_query())

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.data["files_processed"], 0)
        self.assertEqual(response.data["files_total"], 0)

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_presign_ttl(self, mock_s3_client, _mock_kafka):
        """BH-INT-027 / BH-INT-022: presigned URLs use 48h ROS_URL_EXPIRATION."""
        prefix = f"{self.schema}/source={self.provider_uuid}/date=2026-01-01"
        mock_client = self._mock_s3_list({prefix: [f"{prefix}/x.csv"]})
        mock_s3_client.return_value = mock_client

        self._post(self._valid_query(start="2026-01-01", end="2026-01-01"))

        mock_client.generate_presigned_url.assert_called_once()
        self.assertEqual(
            mock_client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            masu_config.ROS_URL_EXPIRATION,
        )
        self.assertEqual(masu_config.ROS_URL_EXPIRATION, 172800)

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message", side_effect=RuntimeError("kafka down"))
    @patch("masu.api.reship_ros.generate_s3_object_url", return_value="https://s3/presigned")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_kafka_failure_5xx(self, mock_s3_client, *_mocks):
        """BH-INT-026 (plan): Kafka publish failure → 500."""
        prefix = f"{self.schema}/source={self.provider_uuid}/date=2026-01-01"
        mock_s3_client.return_value = self._mock_s3_list({prefix: [f"{prefix}/x.csv"]})

        response = self._post(self._valid_query(start="2026-01-01", end="2026-01-01"))

        self.assertEqual(response.status_code, HTTPStatus.INTERNAL_SERVER_ERROR)

    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_s3_list_failure(self, mock_s3_client):
        """BH-INT-027 (plan): S3 list failure → 500."""
        from botocore.exceptions import ClientError

        mock_s3_client.return_value.list_objects_v2.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "ListObjectsV2"
        )

        response = self._post(self._valid_query(start="2026-01-01", end="2026-01-01"))

        self.assertEqual(response.status_code, HTTPStatus.INTERNAL_SERVER_ERROR)

    @override_settings(S3_ROS_ACCESS_KEY="", S3_ROS_SECRET="", S3_ROS_BUCKET_NAME="")
    def test_reship_ros_missing_s3_credentials(self):
        """BH-INT-036: missing ROS S3 config → 503."""
        response = self._post(self._valid_query())
        self.assertEqual(response.status_code, HTTPStatus.SERVICE_UNAVAILABLE)

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.generate_s3_object_url", return_value="https://s3/presigned")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_lists_date_prefix_per_day(self, mock_s3_client, *_mocks):
        """BH-INT-041: one ListObjectsV2 call per day in range."""
        mock_client = self._mock_s3_list({})
        mock_s3_client.return_value = mock_client

        self._post(self._valid_query(start="2026-01-10", end="2026-01-12"))

        expected_days = [date(2026, 1, 10), date(2026, 1, 11), date(2026, 1, 12)]
        prefixes = [call.kwargs["Prefix"] for call in mock_client.list_objects_v2.call_args_list]
        for day in expected_days:
            self.assertIn(f"{self.schema}/source={self.provider_uuid}/date={day}", prefixes)

    # ------------------------------------------------------------------
    # #776 (V15-26): manifest-targeted reship and S3 metadata key case.
    # S3 stores user metadata keys lowercase; the filter must match the
    # stored form (or normalize). These are the manifest-targeted path's
    # first tests — the bug survived because the path was untested.

    def _mock_s3_head(self, behavior_by_key: dict):
        """head_object mock: key -> Metadata dict, or an exception to raise."""

        def head_object(**kwargs):
            behavior = behavior_by_key.get(kwargs["Key"])
            if isinstance(behavior, Exception):
                raise behavior
            return {"Metadata": behavior}

        mock_client = MagicMock()
        mock_client.head_object.side_effect = head_object
        return mock_client

    def test_filter_keys_standard_lowercase_metadata(self):
        """#776: S3's standard lowercase metadata keys must match."""
        client = self._mock_s3_head(
            {
                "match.csv": {"manifestid": "42"},
                "other-id.csv": {"manifestid": "43"},
                "no-meta.csv": {},
            }
        )
        matched = _filter_keys_by_manifest_metadata(client, ["match.csv", "other-id.csv", "no-meta.csv"], 42)
        self.assertEqual(matched, ["match.csv"])

    def test_filter_keys_mixed_case_control(self):
        """A case-preserving store's shape — the baseline's only matching
        form, pinned as a control for the normalization."""
        client = self._mock_s3_head({"a.csv": {"ManifestId": "42"}})
        self.assertEqual(_filter_keys_by_manifest_metadata(client, ["a.csv"], 42), ["a.csv"])

    def test_filter_keys_head_object_error_skipped(self):
        """head-object failures skip the key without crashing."""
        client = self._mock_s3_head(
            {
                "bad.csv": ClientError({"Error": {"Code": "404"}}, "HeadObject"),
                # Mixed-case so this test isolates the error path from the
                # lowercase fix (passes on both the baseline and the fix).
                "good.csv": {"ManifestId": "42"},
            }
        )
        matched = _filter_keys_by_manifest_metadata(client, ["bad.csv", "good.csv"], 42)
        self.assertEqual(matched, ["good.csv"])

    def test_filter_keys_empty_input(self):
        """No keys in, no keys out; empty metadata never matches."""
        client = self._mock_s3_head({"a.csv": {}})
        self.assertEqual(_filter_keys_by_manifest_metadata(client, [], 42), [])
        self.assertEqual(_filter_keys_by_manifest_metadata(client, ["a.csv"], 42), [])

    @override_settings(DISABLE_ROS_MSG=False)
    @patch("masu.api.reship_ros.publish_ros_kafka_message")
    @patch("masu.api.reship_ros.generate_s3_object_url", return_value="https://s3/presigned")
    @patch("masu.api.reship_ros.get_ros_s3_client")
    def test_reship_ros_manifest_targeted_lowercase_metadata(self, mock_s3_client, _mock_presign, mock_kafka):
        """#776: a manifest-targeted request recovers the manifest's objects
        when S3 returns standard lowercase metadata keys."""
        manifest = baker.make(
            "CostUsageReportManifest",
            provider_id=self.provider_uuid,
            assembly_id="assembly-776",
            billing_period_start_datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        prefix = f"{self.schema}/source={self.provider_uuid}/date=2026-01-01"
        client = self._mock_s3_list({prefix: ["file1.csv", "file2.csv"]})

        def head_object(**kwargs):
            if kwargs["Key"] == "file1.csv":
                return {"Metadata": {"manifestid": str(manifest.id)}}
            return {"Metadata": {"manifestid": str(manifest.id + 1)}}

        client.head_object.side_effect = head_object
        mock_s3_client.return_value = client

        response = self._post(f"?schema={self.schema}&provider_uuid={self.provider_uuid}&manifest_id=assembly-776")

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertEqual(response.data["files_processed"], 1)
        self.assertEqual(response.data["files_total"], 1)
        self.assertEqual(mock_kafka.call_count, 1)
        # Pin WHICH object was published — a filter selecting file2.csv
        # would pass the count assertions above (review finding).
        published = json.loads(mock_kafka.call_args.args[0].decode("utf-8"))
        self.assertEqual(published["object_keys"], ["file1.csv"])
