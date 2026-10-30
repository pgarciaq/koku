#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ROS OCP cost-groups sync (#675)."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path
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

    def test_build_payload_filters_unallocated_pseudo_row(self):
        """The migration-seeded attribution bucket never ships (#712).

        "Platform unallocated" (migration 0315 seed, present on every tenant)
        is a cost-attribution bucket, not a k8s namespace — the ROS validator
        rejects exact entries containing whitespace, so shipping it 400s the
        whole push. The real seeded defaults must still ship.
        """
        payload = build_costgroups_payload(self.schema)
        names = {entry["namespace"] for entry in payload["entries"]}
        self.assertNotIn("Platform unallocated", names)
        self.assertIn("openshift", names)
        self.assertIn("kube-%", names)
        self.assertIn("openshift-%", names)

    def test_build_payload_warns_and_skips_whitespace_exact_rows(self):
        """Admin-added junk must not 400 the whole push (#712)."""
        with schema_context(self.schema):
            OpenshiftCostCategoryNamespace.objects.create(
                namespace="cg-sync my team",
                system_default=False,
                cost_category=OpenshiftCostCategory.objects.get(name="Platform"),
            )

            def _remove_junk_row():
                with schema_context(self.schema):
                    OpenshiftCostCategoryNamespace.objects.filter(namespace="cg-sync my team").delete()

            self.addCleanup(_remove_junk_row)
        payload = build_costgroups_payload(self.schema)
        names = {entry["namespace"] for entry in payload["entries"]}
        self.assertNotIn("cg-sync my team", names)
        # Valid rows on both sides of the junk row still ship.
        self.assertIn("cg-sync-team", names)
        self.assertIn("cg-sync-admin", names)

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


class RosCostgroupsWorkerRegistrationTest(MasuTestCase):
    """Worker-startup registration (#712).

    Workers only register tasks whose modules their startup loading imports.
    The cost-groups tasks have no import chain (their only importers are
    function-level in the API views), so dispatches were silently discarded.
    This test loads modules exactly the way a fresh worker process does — in
    a subprocess, so in-process imports by other tests cannot mask a missing
    registration.
    """

    def test_tasks_registered_under_worker_module_loading(self):
        code = textwrap.dedent(
            """
            import django
            django.setup()
            from koku.celery import app
            app.loader.import_default_modules()
            missing = [
                name
                for name in (
                    "masu.processor.ros_costgroups_sync.sync_ros_ocp_costgroups",
                    "masu.processor.ros_costgroups_sync.sync_ros_ocp_costgroups_periodic",
                )
                if name not in app.tasks
            ]
            if missing:
                print("worker registry missing: " + ", ".join(missing))
                raise SystemExit(1)
            """
        )
        # Run from the Django project root (manage.py's directory) so the
        # koku package and its settings resolve exactly as under manage.py.
        project_root = str(Path(__file__).resolve().parents[3])
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=project_root,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "koku.settings"},
        )
        self.assertEqual(
            0,
            result.returncode,
            f"worker-style module loading does not register the cost-groups tasks:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr[-2000:]}",
        )
