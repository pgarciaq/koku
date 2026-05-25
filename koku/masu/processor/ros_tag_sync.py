#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Sync enabled OCP tags from Koku to ros-ocp-backend."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import timedelta

import requests
from django.conf import settings
from django_tenants.utils import schema_context

from api.common import log_json
from api.provider.models import Provider
from api.utils import DateHelper
from common.queues import PriorityQueue
from koku import celery_app
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.ocp.models import OCPUsageLineItemDailySummary

LOG = logging.getLogger(__name__)

DEFAULT_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
TAG_SYNC_PATH = "/api/cost-management/v1/internal/tags/sync"
TAG_LOOKBACK_DAYS = 7


def org_id_from_schema(schema_name: str) -> str:
    """Return bare org_id from a tenant schema name."""
    if schema_name.startswith("org"):
        return schema_name[3:]
    return schema_name


def schedule_ros_tag_sync(schema_name: str) -> None:
    """Queue tag sync when the feature gate is enabled."""
    if not settings.ROS_TAGS_ENABLED:
        return
    sync_ros_ocp_tags.delay(schema_name)


def _read_bearer_token() -> str | None:
    dev_token = getattr(settings, "ROS_TAGS_DEV_TOKEN", None)
    if dev_token:
        return dev_token

    token_path = getattr(settings, "ROS_TAGS_SA_TOKEN_PATH", DEFAULT_SA_TOKEN_PATH)
    try:
        with open(token_path, encoding="utf-8") as token_file:
            token = token_file.read().strip()
            return token or None
    except OSError:
        LOG.warning(log_json(msg="service account token unavailable for ROS tag sync", path=token_path))
        return None


def _extract_enabled_tags(labels: dict | None, enabled_keys: set[str]) -> dict[str, str]:
    if not labels or not enabled_keys:
        return {}
    resolved: dict[str, str] = {}
    for key in enabled_keys:
        if key in labels and labels[key] is not None:
            resolved[key] = str(labels[key])
    return resolved


def build_namespace_tags_payload(schema_name: str) -> dict:
    """Build ros-ocp-backend tag sync payload for a tenant schema."""
    org_id = org_id_from_schema(schema_name)
    dh = DateHelper()
    lookback_start = dh.today - timedelta(days=TAG_LOOKBACK_DAYS)

    with schema_context(schema_name):
        enabled_keys = set(
            EnabledTagKeys.objects.filter(provider_type=Provider.PROVIDER_OCP, enabled=True).values_list(
                "key", flat=True
            )
        )
        if not enabled_keys:
            return {"org_id": org_id, "namespace_tags": []}

        rows = (
            OCPUsageLineItemDailySummary.objects.filter(usage_start__gte=lookback_start)
            .exclude(namespace__isnull=True)
            .exclude(namespace="")
            .exclude(cluster_id__isnull=True)
            .exclude(cluster_id="")
            .values("cluster_id", "namespace", "all_labels")
            .distinct()
        )

        namespace_tags: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        for row in rows:
            tags = _extract_enabled_tags(row.get("all_labels"), enabled_keys)
            if not tags:
                continue
            cluster_namespace = (row["cluster_id"], row["namespace"])
            namespace_tags[cluster_namespace].update(tags)

    payload_tags = [
        {
            "cluster_uuid": cluster_id,
            "namespace": namespace,
            "tags": tags,
        }
        for (cluster_id, namespace), tags in sorted(namespace_tags.items())
    ]
    return {"org_id": org_id, "namespace_tags": payload_tags}


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
    if not settings.ROS_TAGS_ENABLED:
        LOG.debug(log_json(tracing_id, msg="ROS tag sync disabled", schema=schema_name))
        return

    context = {"schema": schema_name, "org_id": org_id_from_schema(schema_name)}
    try:
        payload = build_namespace_tags_payload(schema_name)
        updated = push_namespace_tags(schema_name, payload=payload)
        LOG.info(
            log_json(
                tracing_id,
                msg="ROS tag sync completed",
                context={**context, "namespace_count": len(payload["namespace_tags"]), "updated": updated},
            )
        )
    except Exception as exc:
        LOG.error(log_json(tracing_id, msg="ROS tag sync failed", context=context, error=str(exc)))
        raise
