#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the user_currency endpoint view."""
from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse
from django_tenants.utils import schema_context

from masu.test import MasuTestCase
from reporting.user_settings.models import UserSettings


@override_settings(ROOT_URLCONF="masu.urls")
class UserCurrencyTest(MasuTestCase):
    """Test Cases for the user_currency endpoint."""

    @patch("koku.middleware.MASU", return_value=True)
    def test_returns_default_currency_when_no_settings(self, _):
        """Test that USD is returned when no UserSettings row exists."""
        with schema_context(self.schema):
            UserSettings.objects.all().delete()

        response = self.client.get(reverse("user_currency"), {"org_id": self.org_id})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["currency"], "USD")

    @patch("koku.middleware.MASU", return_value=True)
    def test_returns_configured_currency(self, _):
        """Test that the configured currency is returned from UserSettings."""
        with schema_context(self.schema):
            UserSettings.objects.all().delete()
            UserSettings.objects.create(settings={"currency": "EUR", "cost_type": "unblended_cost"})

        try:
            response = self.client.get(reverse("user_currency"), {"org_id": self.org_id})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["currency"], "EUR")
        finally:
            with schema_context(self.schema):
                UserSettings.objects.all().delete()

    @patch("koku.middleware.MASU", return_value=True)
    def test_missing_org_id_returns_400(self, _):
        """Test that omitting org_id returns 400."""
        response = self.client.get(reverse("user_currency"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Error", response.json())

    @patch("koku.middleware.MASU", return_value=True)
    def test_nonexistent_org_id_returns_404(self, _):
        """Test that a non-existent org_id returns 404."""
        response = self.client.get(reverse("user_currency"), {"org_id": "9999999"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("Error", response.json())
