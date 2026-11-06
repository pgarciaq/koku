#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Sync enabled OCP tags from Koku to ros-ocp-backend."""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from datetime import timezone

import requests
from django.conf import settings
from django_tenants.utils import schema_context

from api.common import log_json
from api.iam.models import Tenant
from api.provider.models import Provider
from api.utils import DateHelper
from common.queues import PriorityQueue
from koku import celery_app
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.ocp.models import OCPStorageVolumeLabelSummary
from reporting.provider.ocp.models import OCPUsagePodLabelSummary

LOG = logging.getLogger(__name__)

DEFAULT_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
TAG_SYNC_PATH = "/api/cost-management/v1/internal/tags/sync"


def ros_tags_push_enabled() -> bool:
    """Return True when Koku should push tags to ROS via HTTP (SaaS / api source)."""
    return bool(settings.ROS_TAGS_ENABLED) and getattr(settings, "ROS_TAGS_SOURCE", "db") == "api"


def org_id_from_schema(schema_name: str) -> str:
    """Return bare org_id from a tenant schema name."""
    if schema_name.startswith("org"):
        return schema_name[3:]
    return schema_name


def schedule_ros_tag_sync(schema_name: str) -> None:
    """Queue tag sync when the feature gate and api source are enabled."""
    if not ros_tags_push_enabled():
        return
    sync_ros_ocp_tags.delay(schema_name)


def _read_bearer_token() -> str | None:
    dev_token = getattr(settings, "ROS_TAGS_DEV_TOKEN", None)
    if dev_token:
        return dev_token

    token_path = (
        os.environ.get("ROS_SA_TOKEN_PATH")
        or getattr(settings, "ROS_TAGS_SA_TOKEN_PATH", None)
        or DEFAULT_SA_TOKEN_PATH
    )
    try:
        with open(token_path, encoding="utf-8") as token_file:
            token = token_file.read().strip()
            return token or None
    except OSError:
        LOG.warning(log_json(msg="service account token unavailable for ROS tag sync", path=token_path))
        return None


def _tag_window(dh: DateHelper) -> tuple:
    """Return the report-period window Koku's tags API uses for its default request.

    ``TagQueryHandler._set_start_and_end_dates`` selects the previous month start
    for the default ``time_scope=-30`` and ``_get_time_based_filters`` snaps both
    ends to whole months, so the window is last month through the end of this
    month.
    """
    return dh.last_month_start, dh.this_month_end


def _iter_label_rows(schema_name: str, enabled_keys: list[str]):
    """Yield (cluster_id, namespace, key, values) from Koku's label summary tables.

    These are the tables behind Koku's tags API: one row per
    (key, report period, namespace, node). Pod and storage labels are separate
    sources there and are merged here the same way. Because rows accumulate
    within a report period, a cluster whose last report is older than another
    cluster's keeps its tags until its period leaves the window.
    """
    start, end = _tag_window(DateHelper())
    with schema_context(schema_name):
        for model in (OCPUsagePodLabelSummary, OCPStorageVolumeLabelSummary):
            rows = (
                model.objects.filter(
                    key__in=enabled_keys,
                    report_period__report_period_start__gte=start,
                    report_period__report_period_start__lte=end,
                )
                .values_list("report_period__cluster_id", "namespace", "key", "values")
                .iterator()
            )
            yield from rows


def build_namespace_tags_payload(schema_name: str) -> dict:
    """Build ros-ocp-backend tag sync payload for a tenant schema."""
    org_id = org_id_from_schema(schema_name)
    synced_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with schema_context(schema_name):
        enabled_keys = sorted(
            EnabledTagKeys.objects.filter(provider_type=Provider.PROVIDER_OCP, enabled=True).values_list(
                "key", flat=True
            )
        )
    if not enabled_keys:
        return {
            "org_id": org_id,
            "synced_at": synced_at,
            "tag_keys": [],
            "namespace_tags": [],
        }

    namespace_values: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    key_values: dict[str, set[str]] = defaultdict(set)

    for cluster_id, namespace, key, values in _iter_label_rows(schema_name, enabled_keys):
        if not cluster_id or not namespace:
            continue
        for value in values:
            if value is None:
                continue
            namespace_values[(cluster_id, namespace)][key].add(value)
            key_values[key].add(value)

    payload_tags = [
        {
            "cluster_uuid": cluster_id,
            "namespace": namespace,
            # Koku keeps a list of values per key; resolved_tags holds one, so
            # take the lowest deterministically rather than last-row-wins.
            "tags": {key: sorted(values)[0] for key, values in sorted(tags.items()) if values},
        }
        for (cluster_id, namespace), tags in sorted(namespace_values.items())
    ]
    tag_keys = [{"key": key, "values": sorted(key_values.get(key, set()))} for key in enabled_keys]
    return {
        "org_id": org_id,
        "synced_at": synced_at,
        "tag_keys": tag_keys,
        "namespace_tags": payload_tags,
    }


def push_namespace_tags(schema_name: str, payload: dict | None = None) -> int:
    """POST namespace tag payload to ros-ocp-backend. Returns updated row count."""
    if payload is None:
        payload = build_namespace_tags_payload(schema_name)

    backend_url = settings.ROS_OCP_BACKEND_URL.rstrip("/")
    url = f"{backend_url}{TAG_SYNC_PATH}"

    bearer_token = _read_bearer_token()
    if not bearer_token:
        raise RuntimeError("ROS tag sync bearer token is not configured")

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    response.raise_for_status()
    body = response.json()
    return int(body.get("updated", 0))


@celery_app.task(name="masu.processor.ros_tag_sync.sync_ros_ocp_tags", queue=PriorityQueue.DEFAULT)
def sync_ros_ocp_tags(schema_name: str, tracing_id: str | None = None) -> None:
    """Sync enabled OCP tags to ros-ocp-backend for a tenant."""
    if not ros_tags_push_enabled():
        LOG.debug(log_json(tracing_id, msg="ROS tag push sync disabled", schema=schema_name))
        return

    context = {"schema": schema_name, "org_id": org_id_from_schema(schema_name)}
    try:
        payload = build_namespace_tags_payload(schema_name)
        updated = push_namespace_tags(schema_name, payload=payload)
        LOG.info(
            log_json(
                tracing_id,
                msg="ROS tag sync completed",
                context={
                    **context,
                    "namespace_count": len(payload["namespace_tags"]),
                    "tag_key_count": len(payload["tag_keys"]),
                    "synced_at": payload["synced_at"],
                    "updated": updated,
                },
            )
        )
    except Exception as exc:
        LOG.error(log_json(tracing_id, msg="ROS tag sync failed", context=context, error=str(exc)))
        raise


@celery_app.task(name="masu.processor.ros_tag_sync.sync_ros_ocp_tags_periodic", queue=PriorityQueue.DEFAULT)
def sync_ros_ocp_tags_periodic(tracing_id: str | None = None) -> None:
    """Safety-net sync: queue tag sync for every tenant when push sync is enabled."""
    if not ros_tags_push_enabled():
        LOG.debug(log_json(tracing_id, msg="ROS periodic tag push sync disabled"))
        return

    schema_names = [
        tenant["schema_name"]
        for tenant in Tenant.objects.values("schema_name")
        if tenant.get("schema_name") and tenant["schema_name"] != "public"
    ]

    for schema_name in schema_names:
        sync_ros_ocp_tags.delay(schema_name, tracing_id=tracing_id)

    LOG.info(
        log_json(
            tracing_id,
            msg="ROS periodic tag sync scheduled",
            context={"tenant_count": len(schema_names)},
        )
    )
