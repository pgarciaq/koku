#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Sync Platform cost-group namespaces from Koku to ros-ocp-backend."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from datetime import timezone

import requests
from django.conf import settings
from django_tenants.utils import schema_context

from api.common import log_json
from api.iam.models import Tenant
from common.queues import PriorityQueue
from koku import celery_app
from masu.processor.ros_tag_sync import org_id_from_schema
from reporting.provider.ocp.models import OpenshiftCostCategoryNamespace

LOG = logging.getLogger(__name__)

DEFAULT_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
COSTGROUPS_SYNC_PATH = "/api/cost-management/v1/internal/cost-groups/sync"
PLATFORM_GROUP_NAME = "Platform"


def ros_costgroups_push_enabled() -> bool:
    """Return True when Koku should push cost groups to ROS via HTTP (api source)."""
    return bool(settings.ROS_COSTGROUPS_ENABLED) and getattr(settings, "ROS_COSTGROUPS_SOURCE", "db") == "api"


def schedule_ros_ocp_costgroups(schema_name: str) -> None:
    """Queue cost-groups sync when the feature gate and api source are enabled."""
    if not ros_costgroups_push_enabled():
        return
    sync_ros_ocp_costgroups.delay(schema_name)


def _read_bearer_token() -> str | None:
    """Read the SA bearer token for ROS pushes.

    Same cluster SA and ROS backend as tag sync, so the same settings apply
    (duplicated here to keep masu.processor.ros_tag_sync untouched).
    """
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
        LOG.warning(log_json(msg="service account token unavailable for ROS cost-groups sync", path=token_path))
        return None


def build_costgroups_payload(schema_name: str) -> dict:
    """Build ros-ocp-backend cost-groups sync payload for a tenant schema.

    The whole Platform group ships — shipped defaults and admin-added rows
    alike (system_default is provenance metadata only). Koku `%`-wildcard
    rows pass through verbatim with is_prefix set; ROS strips the suffix.
    """
    org_id = org_id_from_schema(schema_name)
    synced_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with schema_context(schema_name):
        rows = (
            OpenshiftCostCategoryNamespace.objects.filter(cost_category__name=PLATFORM_GROUP_NAME)
            .values("namespace", "system_default")
            .order_by("namespace")
        )
        entries = [
            {
                "namespace": row["namespace"],
                "is_prefix": row["namespace"].endswith("%"),
                "system_default": bool(row["system_default"]),
            }
            for row in rows
        ]

    return {
        "org_id": org_id,
        "synced_at": synced_at,
        "entries": entries,
    }


def push_costgroups(schema_name: str, payload: dict | None = None) -> int:
    """POST cost-groups payload to ros-ocp-backend. Returns updated row count."""
    if payload is None:
        payload = build_costgroups_payload(schema_name)

    backend_url = settings.ROS_OCP_BACKEND_URL.rstrip("/")
    url = f"{backend_url}{COSTGROUPS_SYNC_PATH}"

    bearer_token = _read_bearer_token()
    if not bearer_token:
        raise RuntimeError("ROS cost-groups sync bearer token is not configured")

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
    response.raise_for_status()
    body = response.json()
    return int(body.get("updated", 0))


@celery_app.task(name="masu.processor.ros_costgroups_sync.sync_ros_ocp_costgroups", queue=PriorityQueue.DEFAULT)
def sync_ros_ocp_costgroups(schema_name: str, tracing_id: str | None = None) -> None:
    """Sync Platform cost-group namespaces to ros-ocp-backend for a tenant."""
    if not ros_costgroups_push_enabled():
        LOG.debug(log_json(tracing_id, msg="ROS cost-groups push sync disabled", schema=schema_name))
        return

    context = {"schema": schema_name, "org_id": org_id_from_schema(schema_name)}
    try:
        payload = build_costgroups_payload(schema_name)
        updated = push_costgroups(schema_name, payload=payload)
        LOG.info(
            log_json(
                tracing_id,
                msg="ROS cost-groups sync completed",
                context={
                    **context,
                    "entry_count": len(payload["entries"]),
                    "synced_at": payload["synced_at"],
                    "updated": updated,
                },
            )
        )
    except Exception as exc:
        LOG.error(log_json(tracing_id, msg="ROS cost-groups sync failed", context=context, error=str(exc)))
        raise


@celery_app.task(
    name="masu.processor.ros_costgroups_sync.sync_ros_ocp_costgroups_periodic", queue=PriorityQueue.DEFAULT
)
def sync_ros_ocp_costgroups_periodic(tracing_id: str | None = None) -> None:
    """Safety-net sync: queue cost-groups sync for every tenant when push sync is enabled."""
    if not ros_costgroups_push_enabled():
        LOG.debug(log_json(tracing_id, msg="ROS periodic cost-groups push sync disabled"))
        return

    schema_names = [
        tenant["schema_name"]
        for tenant in Tenant.objects.values("schema_name")
        if tenant.get("schema_name") and tenant["schema_name"] != "public"
    ]

    for schema_name in schema_names:
        sync_ros_ocp_costgroups.delay(schema_name, tracing_id=tracing_id)

    LOG.info(
        log_json(
            tracing_id,
            msg="ROS periodic cost-groups sync scheduled",
            context={"tenant_count": len(schema_names)},
        )
    )
