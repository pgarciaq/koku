#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Masu API to re-publish existing ROS reports from S3 to Kafka for re-ingestion."""
import json
import logging
from http import HTTPStatus
from uuid import UUID
from uuid import uuid4

from botocore.exceptions import BotoCoreError
from botocore.exceptions import ClientError
from django.conf import settings
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.decorators import renderer_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings

from api.common import log_json
from api.provider.models import Provider
from api.provider.models import Sources
from api.utils import DateHelper
from kafka_utils.utils import delivery_callback
from kafka_utils.utils import get_producer
from kafka_utils.utils import ROS_TOPIC
from masu.external.ros_report_shipper import generate_s3_object_url
from masu.external.ros_report_shipper import get_ros_s3_client
from masu.prometheus_stats import KAFKA_CONNECTION_ERRORS_COUNTER
from masu.util.ocp import common as ocp_utils

LOG = logging.getLogger(__name__)


def _missing_ros_s3_credentials() -> bool:
    """Return True when ROS S3 credentials are not configured."""
    return not all((settings.S3_ROS_ACCESS_KEY, settings.S3_ROS_SECRET, settings.S3_ROS_BUCKET_NAME))


def _ros_date_prefix(schema: str, provider_uuid: str, day) -> str:
    """S3 prefix for ROS objects on a single day."""
    return f"{schema}/source={provider_uuid}/date={day}"


def _org_id_from_schema(schema: str) -> str:
    if schema.startswith("org"):
        return schema[3:]
    return schema


def _lookup_provider_metadata(provider_uuid: str, schema: str) -> dict | None:
    """Resolve Kafka metadata fields for a provider UUID."""
    try:
        provider = Provider.objects.get(uuid=provider_uuid)
    except Provider.DoesNotExist:
        return None

    source = Sources.objects.filter(koku_uuid=str(provider_uuid)).first()
    cluster_uuid = ocp_utils.get_cluster_id_from_provider(provider_uuid)
    if not cluster_uuid:
        return None

    account = provider.account.get("account_id") if provider.account else None
    org_id = (source.org_id if source and source.org_id else None) or _org_id_from_schema(schema)
    source_id = str(source.source_id) if source else "0"
    b64_identity = source.auth_header if source and source.auth_header else ""

    return {
        "account": account or "",
        "org_id": org_id,
        "source_id": source_id,
        "provider_uuid": str(provider_uuid),
        "cluster_uuid": cluster_uuid,
        "operator_version": "",
        "cluster_alias": provider.name,
        "b64_identity": b64_identity,
    }


def build_ros_kafka_message(
    request_id: str,
    b64_identity: str,
    metadata: dict,
    presigned_url: str,
    upload_key: str,
) -> bytes:
    """Build a ROS Kafka message matching ROSReportShipper.build_ros_msg format."""
    ros_json = {
        "request_id": request_id,
        "b64_identity": b64_identity,
        "metadata": metadata,
        "files": [presigned_url],
        "object_keys": [upload_key],
    }
    return bytes(json.dumps(ros_json), "utf-8")


def list_ros_s3_keys(s3_client, schema: str, provider_uuid: str, start_date, end_date) -> list[str]:
    """List ROS object keys in S3 for each day in the inclusive date range."""
    dh = DateHelper()
    keys: list[str] = []
    for day in dh.list_days(start_date, end_date):
        prefix = _ros_date_prefix(schema, provider_uuid, day)
        continuation_token = None
        while True:
            list_kwargs = {"Bucket": settings.S3_ROS_BUCKET_NAME, "Prefix": prefix}
            if continuation_token:
                list_kwargs["ContinuationToken"] = continuation_token
            response = s3_client.list_objects_v2(**list_kwargs)
            for obj in response.get("Contents", []):
                key = obj.get("Key")
                if key and not key.endswith("/"):
                    keys.append(key)
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
    return keys


@KAFKA_CONNECTION_ERRORS_COUNTER.count_exceptions()
def publish_ros_kafka_message(msg: bytes) -> None:
    """Publish a single message to the ROS Kafka topic."""
    producer = get_producer()
    producer.produce(ROS_TOPIC, value=msg, callback=delivery_callback)
    producer.poll(0)


def _parse_reship_params(params) -> tuple[dict | None, Response | None]:
    """Validate query params; return parsed values or an error Response."""
    schema = params.get("schema")
    provider_uuid = params.get("provider_uuid")
    start_date_raw = params.get("start_date")
    end_date_raw = params.get("end_date")

    if not schema:
        return None, Response({"error": "schema is required"}, status=HTTPStatus.BAD_REQUEST)
    if not provider_uuid:
        return None, Response({"error": "provider_uuid is required"}, status=HTTPStatus.BAD_REQUEST)
    if not start_date_raw or not end_date_raw:
        return None, Response({"error": "start_date and end_date are required"}, status=HTTPStatus.BAD_REQUEST)

    try:
        UUID(provider_uuid)
    except ValueError:
        return None, Response({"error": "provider_uuid is invalid"}, status=HTTPStatus.BAD_REQUEST)

    dh = DateHelper()
    try:
        start_date = dh.parse_to_date(start_date_raw)
        end_date = dh.parse_to_date(end_date_raw)
    except (TypeError, ValueError):
        return None, Response({"error": "start_date and end_date must be YYYY-MM-DD"}, status=HTTPStatus.BAD_REQUEST)

    if end_date < start_date:
        return None, Response({"error": "end_date must be on or after start_date"}, status=HTTPStatus.BAD_REQUEST)

    return {
        "schema": schema,
        "provider_uuid": provider_uuid,
        "start_date": start_date,
        "end_date": end_date,
    }, None


def _reship_response(request_id: str, files_processed: int, files_total: int) -> Response:
    return Response(
        {
            "request_id": request_id,
            "files_processed": files_processed,
            "files_total": files_total,
        },
        status=HTTPStatus.OK,
    )


def _publish_reship_messages(
    request_id: str,
    s3_client,
    object_keys: list[str],
    b64_identity: str,
    kafka_metadata: dict,
) -> int:
    """Presign each key and publish one Kafka message per object."""
    files_processed = 0
    for upload_key in object_keys:
        presigned_url = generate_s3_object_url(s3_client, upload_key)
        kafka_msg = build_ros_kafka_message(request_id, b64_identity, kafka_metadata, presigned_url, upload_key)
        publish_ros_kafka_message(kafka_msg)
        files_processed += 1
    return files_processed


@never_cache
@api_view(http_method_names=["POST"])
@permission_classes((AllowAny,))
@renderer_classes(tuple(api_settings.DEFAULT_RENDERER_CLASSES))
def reship_ros(request):
    """
    List existing ROS CSV objects in S3 for a provider/date range, presign URLs, and publish Kafka messages.

    Query parameters:
        schema: Tenant schema (e.g. org1234567)
        provider_uuid: Provider UUID for the cluster
        start_date: Inclusive start date (YYYY-MM-DD)
        end_date: Inclusive end date (YYYY-MM-DD)
    """
    request_id = uuid4().hex
    parsed, error_response = _parse_reship_params(request.query_params)
    if error_response:
        return error_response

    schema = parsed["schema"]
    provider_uuid = parsed["provider_uuid"]
    start_date = parsed["start_date"]
    end_date = parsed["end_date"]

    if _missing_ros_s3_credentials():
        return Response(
            {"error": "ROS S3 credentials are not configured"},
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        )

    provider_metadata = _lookup_provider_metadata(provider_uuid, schema)
    if not provider_metadata:
        return Response({"error": "provider_uuid is invalid"}, status=HTTPStatus.BAD_REQUEST)

    b64_identity = provider_metadata.pop("b64_identity")
    kafka_metadata = provider_metadata

    try:
        s3_client = get_ros_s3_client()
        object_keys = list_ros_s3_keys(s3_client, schema, provider_uuid, start_date, end_date)
    except (BotoCoreError, ClientError) as err:
        msg = f"Unable to list ROS objects in bucket {settings.S3_ROS_BUCKET_NAME}: {err}"
        LOG.error(log_json(request_id, msg=msg, schema=schema, provider_uuid=provider_uuid))
        return Response({"error": msg}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    files_total = len(object_keys)
    if not object_keys:
        return _reship_response(request_id, 0, 0)

    if settings.DISABLE_ROS_MSG:
        LOG.info(log_json(request_id, msg="ROS kafka publishing disabled", schema=schema))
        return _reship_response(request_id, 0, files_total)

    try:
        files_processed = _publish_reship_messages(request_id, s3_client, object_keys, b64_identity, kafka_metadata)
    except Exception as err:
        msg = f"Failed to publish ROS Kafka messages: {err}"
        LOG.error(log_json(request_id, msg=msg, schema=schema, provider_uuid=provider_uuid))
        return Response({"error": msg}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    LOG.info(
        log_json(
            request_id,
            msg="ROS reship completed",
            schema=schema,
            provider_uuid=provider_uuid,
            files_processed=files_processed,
            files_total=files_total,
        )
    )
    return _reship_response(request_id, files_processed, files_total)
