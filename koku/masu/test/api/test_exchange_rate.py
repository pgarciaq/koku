#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the exchange_rate endpoint view."""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse
from django_tenants.utils import schema_context

from api.currency.models import ExchangeRateDictionary
from cost_models.models import MonthlyExchangeRate
from masu.test import MasuTestCase


@override_settings(ROOT_URLCONF="masu.urls")
class ExchangeRateTest(MasuTestCase):
    """Test Cases for the exchange_rate endpoint."""

    @patch("koku.middleware.MASU", return_value=True)
    def test_missing_params_returns_400(self, _):
        """Test that omitting required params returns 400."""
        response = self.client.get(reverse("exchange_rate"))
        self.assertEqual(response.status_code, 400)

        response = self.client.get(reverse("exchange_rate"), {"schema": self.schema})
        self.assertEqual(response.status_code, 400)

        response = self.client.get(reverse("exchange_rate"), {"schema": self.schema, "from": "USD"})
        self.assertEqual(response.status_code, 400)

    @patch("koku.middleware.MASU", return_value=True)
    def test_nonexistent_schema_returns_404(self, _):
        """Test that a non-existent schema returns 404."""
        response = self.client.get(
            reverse("exchange_rate"),
            {"schema": "org_nonexistent", "from": "USD", "to": "EUR"},
        )
        self.assertEqual(response.status_code, 404)

    @patch("koku.middleware.MASU", return_value=True)
    def test_same_currency_returns_rate_1(self, _):
        """Test that converting a currency to itself returns rate=1."""
        response = self.client.get(
            reverse("exchange_rate"),
            {"schema": self.schema, "from": "USD", "to": "USD"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["rate"], "1")
        self.assertEqual(body["from_currency"], "USD")
        self.assertEqual(body["to_currency"], "USD")

    @patch("koku.middleware.MASU", return_value=True)
    def test_rate_from_exchange_dictionary(self, _):
        """Test rate lookup from ExchangeRateDictionary (flag-off path)."""
        ExchangeRateDictionary.objects.all().delete()
        ExchangeRateDictionary.objects.create(
            currency_exchange_dictionary={
                "USD": {"EUR": Decimal("0.92"), "GBP": Decimal("0.80")},
                "EUR": {"USD": Decimal("1.087"), "GBP": Decimal("0.869")},
            }
        )
        try:
            response = self.client.get(
                reverse("exchange_rate"),
                {"schema": self.schema, "from": "USD", "to": "EUR"},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIsNotNone(body["rate"])
            self.assertAlmostEqual(float(body["rate"]), 0.92, places=2)
        finally:
            ExchangeRateDictionary.objects.all().delete()

    @patch("koku.middleware.MASU", return_value=True)
    def test_missing_pair_returns_null_rate(self, _):
        """Test that a missing currency pair returns rate=null."""
        ExchangeRateDictionary.objects.all().delete()
        ExchangeRateDictionary.objects.create(currency_exchange_dictionary={"USD": {"EUR": Decimal("0.92")}})
        try:
            response = self.client.get(
                reverse("exchange_rate"),
                {"schema": self.schema, "from": "USD", "to": "JPY"},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIsNone(body["rate"])
        finally:
            ExchangeRateDictionary.objects.all().delete()

    @patch("masu.api.exchange_rate.is_feature_flag_enabled_by_schema", return_value=True)
    @patch("koku.middleware.MASU", return_value=True)
    def test_rate_from_monthly_exchange_rate_when_flag_on(self, _, __):
        """Test rate lookup from MonthlyExchangeRate when constant-currency flag is on."""
        today = datetime.date.today()
        first_of_month = today.replace(day=1)

        with schema_context(self.schema):
            MonthlyExchangeRate.objects.filter(base_currency="USD", target_currency="GBP").delete()
            MonthlyExchangeRate.objects.create(
                effective_date=first_of_month,
                base_currency="USD",
                target_currency="GBP",
                exchange_rate=Decimal("0.79000000000000000"),
                rate_type="static",
            )

        try:
            response = self.client.get(
                reverse("exchange_rate"),
                {"schema": self.schema, "from": "USD", "to": "GBP"},
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertIsNotNone(body["rate"])
            self.assertAlmostEqual(float(body["rate"]), 0.79, places=2)
        finally:
            with schema_context(self.schema):
                MonthlyExchangeRate.objects.filter(base_currency="USD", target_currency="GBP").delete()

    @patch("koku.middleware.MASU", return_value=True)
    def test_currency_codes_are_uppercased(self, _):
        """Test that lowercase currency codes are normalized to uppercase."""
        response = self.client.get(
            reverse("exchange_rate"),
            {"schema": self.schema, "from": "usd", "to": "usd"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["from_currency"], "USD")
        self.assertEqual(body["to_currency"], "USD")
