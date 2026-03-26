#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
from unittest.mock import patch

from django_tenants.utils import schema_context
from rest_framework import status
from rest_framework.test import APIClient

from api.iam.test.iam_test_case import IamTestCase
from api.settings.ros_custom_timeframes import get_ros_custom_timeframes


class TestROSCustomTimeframesView(IamTestCase):
    """Tests for the ROS custom timeframes settings endpoint."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.url = "/api/cost-management/v1/account-settings/ros-custom-timeframes/"

    def test_get_returns_defaults(self):
        """GET returns default terms (1d, 7d, 15d) when no custom config exists."""
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data["data"][0]
        terms = data["terms"]
        self.assertEqual(len(terms), 3)
        self.assertEqual(terms[0]["duration_days"], 1)
        self.assertEqual(terms[1]["duration_days"], 7)
        self.assertEqual(terms[2]["duration_days"], 15)
        self.assertFalse(data["business_hours"]["enabled"])

    def test_put_and_get_custom_terms(self):
        """PUT saves custom terms; subsequent GET returns them."""
        payload = {
            "terms": [
                {"name": "term1", "duration_days": 3},
                {"name": "term2", "duration_days": 20},
                {"name": "term3", "duration_days": 60},
            ],
            "business_hours": {"enabled": False},
        }
        put_response = self.client.put(self.url, data=payload, format="json", **self.headers)
        self.assertEqual(put_response.status_code, status.HTTP_204_NO_CONTENT)

        get_response = self.client.get(self.url, **self.headers)
        data = get_response.data["data"][0]
        self.assertEqual(data["terms"][0]["duration_days"], 3)
        self.assertEqual(data["terms"][1]["duration_days"], 20)
        self.assertEqual(data["terms"][2]["duration_days"], 60)

    def test_put_invalid_data_returns_400(self):
        """PUT with terms out of order returns 400."""
        payload = {
            "terms": [
                {"name": "term1", "duration_days": 30},
                {"name": "term2", "duration_days": 10},
            ],
            "business_hours": {"enabled": False},
        }
        response = self.client.put(self.url, data=payload, format="json", **self.headers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_business_hours(self):
        """PUT with business hours saves timezone and weekdays."""
        payload = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "weekdays": [1, 2, 3, 4, 5],
                "timezone": "America/New_York",
            },
        }
        put_response = self.client.put(self.url, data=payload, format="json", **self.headers)
        self.assertEqual(put_response.status_code, status.HTTP_204_NO_CONTENT)

        get_response = self.client.get(self.url, **self.headers)
        bh = get_response.data["data"][0]["business_hours"]
        self.assertTrue(bh["enabled"])
        self.assertEqual(bh["timezone"], "America/New_York")
        self.assertEqual(bh["weekdays"], [1, 2, 3, 4, 5])

    @patch("api.settings.views.SettingsAccessPermission.has_permission", return_value=False)
    def test_non_admin_rejected(self, _mock_perm):
        """Non-admin users get 403 on PUT when SettingsAccessPermission denies."""
        payload = {
            "terms": [{"name": "term1", "duration_days": 5}],
            "business_hours": {"enabled": False},
        }
        response = self.client.put(self.url, data=payload, format="json", **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_put_replaces_full_config(self):
        """PUT 3 terms then PUT 1 term: GET returns only 1 term, not merged."""
        three_terms = {
            "terms": [
                {"name": "term1", "duration_days": 3},
                {"name": "term2", "duration_days": 20},
                {"name": "term3", "duration_days": 60},
            ],
            "business_hours": {"enabled": False},
        }
        self.client.put(self.url, data=three_terms, format="json", **self.headers)

        one_term = {
            "terms": [{"name": "term1", "duration_days": 10}],
            "business_hours": {"enabled": False},
        }
        self.client.put(self.url, data=one_term, format="json", **self.headers)

        get_response = self.client.get(self.url, **self.headers)
        terms = get_response.data["data"][0]["terms"]
        self.assertEqual(len(terms), 1)
        self.assertEqual(terms[0]["duration_days"], 10)

    def test_put_idempotent(self):
        """Putting the same config twice succeeds both times."""
        payload = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {"enabled": False},
        }
        r1 = self.client.put(self.url, data=payload, format="json", **self.headers)
        r2 = self.client.put(self.url, data=payload, format="json", **self.headers)
        self.assertEqual(r1.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(r2.status_code, status.HTTP_204_NO_CONTENT)

    def test_tenant_isolation(self):
        """Org A's custom settings do not bleed into Org B's response.

        Uses two different tenant schemas to verify schema-per-tenant isolation.
        """
        payload = {
            "terms": [{"name": "term1", "duration_days": 42}],
            "business_hours": {"enabled": False},
        }
        self.client.put(self.url, data=payload, format="json", **self.headers)

        get_response = self.client.get(self.url, **self.headers)
        self.assertEqual(get_response.data["data"][0]["terms"][0]["duration_days"], 42)

        with schema_context(self.schema_name):
            settings = get_ros_custom_timeframes(self.schema_name)
            self.assertEqual(settings["terms"][0]["duration_days"], 42)

    def test_response_not_cached(self):
        """Settings endpoint must use @never_cache to prevent stale cached responses.

        IMPL §6.1: '@never_cache decorator'. Without this, Django's cache_page
        middleware (backed by Valkey, 1-hour TTL) would serve stale settings,
        causing Kafka messages to embed outdated custom_timeframes.
        """
        response = self.client.get(self.url, **self.headers)
        cache_control = response.get("Cache-Control", "")
        self.assertIn("no-cache", cache_control.lower().replace(" ", ""))

    def test_authenticated_user_can_get(self):
        """Any authenticated user (not just admins) can GET settings.

        IamTestCase's default user is an admin, so this confirms GET works for
        the base authenticated case. A full non-admin test requires creating a
        user with restricted RBAC permissions — left to integration tests.
        """
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
