#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Notify ros-ocp-backend to recalculate savings after Koku cost model updates."""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

import requests
from django.conf import settings

from api.common import log_json
from masu.processor.ros_tag_sync import _read_bearer_token
from masu.processor.ros_tag_sync import org_id_from_schema

LOG = logging.getLogger(__name__)

SAVINGS_RECALC_PATH = "/api/cost-management/v1/internal/recalculate-savings"
DEFAULT_RECOMMENDATION_TYPES = ("container", "node", "pvc", "quota", "cluster-quota")


def _ros_backend_base_url() -> str | None:
    """Return ROS API base URL (scheme + host + port), or None when not configured."""
    ros_host = getattr(settings, "ROS_API_HOST", None) or os.environ.get("ROS_API_HOST")
    if ros_host:
        ros_port = getattr(settings, "ROS_API_PORT", None) or os.environ.get("ROS_API_PORT", "8000")
        return f"http://{ros_host}:{ros_port}".rstrip("/")

    backend_url = getattr(settings, "ROS_OCP_BACKEND_URL", None) or os.environ.get("ROS_OCP_BACKEND_URL")
    if not backend_url:
        return None
    parsed = urlparse(backend_url.rstrip("/"))
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return backend_url.rstrip("/")


def _bearer_token_for_ros() -> str | None:
    token = os.environ.get("ROS_SERVICE_TOKEN") or getattr(settings, "ROS_SERVICE_TOKEN", None)
    if token:
        return token
    return _read_bearer_token()


def notify_ros_savings_recalculation(schema_name: str, provider_uuid: str | None = None) -> None:
    """Notify ros-ocp-backend to recalculate savings after a cost model change.

    No-op when ROS is not configured. Failures are logged and do not propagate.
    """
    base_url = _ros_backend_base_url()
    if not base_url:
        LOG.debug(
            log_json(
                msg="ROS savings recalculation skipped: ROS_API_HOST and ROS_OCP_BACKEND_URL not configured",
                schema=schema_name,
            )
        )
        return

    org_id = org_id_from_schema(schema_name)
    url = f"{base_url}{SAVINGS_RECALC_PATH}"
    payload: dict = {
        "org_id": org_id,
        "recommendation_types": list(DEFAULT_RECOMMENDATION_TYPES),
    }
    if provider_uuid:
        payload["cluster_uuid"] = str(provider_uuid)

    headers = {"Content-Type": "application/json"}
    bearer_token = _bearer_token_for_ros()
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in (200, 202):
            LOG.info(
                log_json(
                    msg="ROS savings recalculation triggered",
                    org_id=org_id,
                    provider_uuid=provider_uuid,
                )
            )
        else:
            LOG.warning(
                log_json(
                    msg="ROS savings recalculation request failed",
                    status_code=response.status_code,
                    org_id=org_id,
                    provider_uuid=provider_uuid,
                )
            )
    except Exception as exc:
        LOG.warning(
            log_json(
                msg="Failed to notify ROS for savings recalculation",
                error=str(exc),
                org_id=org_id,
                provider_uuid=provider_uuid,
            )
        )
