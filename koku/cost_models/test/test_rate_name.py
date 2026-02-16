#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for cost model rate name field (PR 1 — TDD RED phase).

These tests verify the new mandatory `name` field on cost model rates,
including serializer validation, uniqueness, API response, and data migration.
All tests are expected to FAIL until the production code is implemented.
"""
from unittest import TestCase

from django.urls import reverse
from rest_framework import serializers
from rest_framework import status
from rest_framework.test import APIClient

from api.iam.test.iam_test_case import IamTestCase
from api.provider.models import Provider
from cost_models.serializers import CostModelSerializer
from cost_models.serializers import RateSerializer


class RateSerializerNameTest(TestCase):
    """T1.1–T1.3: Tests for the name field on RateSerializer."""

    def test_rate_serializer_name_required(self):
        """T1.1: Rate without name is rejected."""
        rate_data = {
            "metric": {"name": "cpu_core_usage_per_hour"},
            "tiered_rates": [{"value": 0.05, "unit": "USD"}],
            "cost_type": "Infrastructure",
        }
        serializer = RateSerializer(data=rate_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)

    def test_rate_serializer_name_max_length(self):
        """T1.2: Rate name exceeding 50 characters is rejected."""
        rate_data = {
            "name": "X" * 51,
            "metric": {"name": "cpu_core_usage_per_hour"},
            "tiered_rates": [{"value": 0.05, "unit": "USD"}],
            "cost_type": "Infrastructure",
        }
        serializer = RateSerializer(data=rate_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)

    def test_rate_serializer_name_accepted(self):
        """T1.3: Rate with valid name is accepted."""
        rate_data = {
            "name": "CPU charge",
            "metric": {"name": "cpu_core_usage_per_hour"},
            "tiered_rates": [{"value": 0.05, "unit": "USD"}],
            "cost_type": "Infrastructure",
        }
        serializer = RateSerializer(data=rate_data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["name"], "CPU charge")


class CostModelSerializerNameTest(IamTestCase):
    """T1.4–T1.6: Tests for rate name uniqueness within a cost model."""

    def test_cost_model_duplicate_rate_names_rejected(self):
        """T1.4: Two rates with the same name in one cost model are rejected."""
        data = {
            "name": "Test Cost Model",
            "source_type": Provider.PROVIDER_OCP,
            "providers": [{"uuid": str(self.provider.uuid), "name": self.provider.name}],
            "rates": [
                {
                    "name": "CPU charge",
                    "metric": {"name": "cpu_core_usage_per_hour"},
                    "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
                {
                    "name": "CPU charge",
                    "metric": {"name": "memory_gb_usage_per_hour"},
                    "tiered_rates": [{"value": 0.03, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
            ],
            "currency": "USD",
        }
        serializer = CostModelSerializer(data=data, context=self.request_context)
        with self.assertRaises(serializers.ValidationError) as ctx:
            serializer.is_valid(raise_exception=True)
        self.assertIn("unique", str(ctx.exception).lower())

    def test_cost_model_unique_rate_names_accepted(self):
        """T1.5: Two rates with different names in one cost model are accepted."""
        data = {
            "name": "Test Cost Model",
            "source_type": Provider.PROVIDER_OCP,
            "providers": [{"uuid": str(self.provider.uuid), "name": self.provider.name}],
            "rates": [
                {
                    "name": "CPU charge",
                    "metric": {"name": "cpu_core_usage_per_hour"},
                    "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
                {
                    "name": "Memory charge",
                    "metric": {"name": "memory_gb_usage_per_hour"},
                    "tiered_rates": [{"value": 0.03, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
            ],
            "currency": "USD",
        }
        serializer = CostModelSerializer(data=data, context=self.request_context)
        self.assertTrue(serializer.is_valid(raise_exception=True))

    def test_cost_model_api_response_includes_name(self):
        """T1.6: GET cost model response includes name in each rate."""
        from cost_models.models import CostModel

        cost_model = CostModel.objects.create(
            name="Test CM for API",
            source_type=Provider.PROVIDER_OCP,
            rates=[
                {
                    "name": "CPU charge",
                    "metric": {"name": "cpu_core_usage_per_hour"},
                    "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
            ],
            currency="USD",
        )
        client = APIClient()
        response = client.get(
            reverse("cost-models-detail", kwargs={"uuid": cost_model.uuid}),
            **self.headers,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rates = response.json()["rates"]
        for rate in rates:
            self.assertIn("name", rate)
            self.assertTrue(len(rate["name"]) > 0)


class RateNameMigrationTest(TestCase):
    """T1.7–T1.11: Tests for the rate name data migration logic.

    Tests the _generate_name function directly. The function is extracted
    as a module-level utility in cost_models.rate_name_utils for testability,
    then imported by the migration.
    """

    def _get_generate_name(self):
        """Import _generate_name from the rate name utilities module.

        This import is inline because the module doesn't exist yet (RED phase).
        """
        from cost_models.rate_name_utils import generate_name

        return generate_name

    def test_migration_generates_name_from_description(self):
        """T1.7: Rate with description gets name derived from description."""
        _generate_name = self._get_generate_name()
        rate = {
            "description": "JBoss middleware license",
            "metric": {"name": "cpu_core_usage_per_hour"},
            "cost_type": "Infrastructure",
        }
        used_names = set()
        name = _generate_name(rate, used_names)
        self.assertEqual(name, "JBoss middleware license")

    def test_migration_truncates_long_description(self):
        """T1.8: Description > 50 chars is truncated to 47 + '...'."""
        _generate_name = self._get_generate_name()
        rate = {
            "description": "A" * 60,
            "metric": {"name": "cpu_core_usage_per_hour"},
            "cost_type": "Infrastructure",
        }
        used_names = set()
        name = _generate_name(rate, used_names)
        self.assertEqual(len(name), 50)
        self.assertTrue(name.endswith("..."))

    def test_migration_generates_name_from_metric_when_no_description(self):
        """T1.9: Rate without description gets name from metric + cost_type."""
        _generate_name = self._get_generate_name()
        rate = {"metric": {"name": "cpu_core_usage_per_hour"}, "cost_type": "Infrastructure"}
        used_names = set()
        name = _generate_name(rate, used_names)
        self.assertEqual(name, "cpu_core_usage_per_hour_infrastructure")

    def test_migration_deduplicates_with_numeric_suffix(self):
        """T1.10: Duplicate candidate names get numeric suffixes."""
        _generate_name = self._get_generate_name()
        rate = {
            "description": "CPU rate",
            "metric": {"name": "cpu_core_usage_per_hour"},
            "cost_type": "Infrastructure",
        }
        used_names = {"CPU rate"}
        name = _generate_name(rate, used_names)
        self.assertEqual(name, "CPU rate_000")
        self.assertNotIn(name, used_names - {name})

    def test_migration_preserves_existing_names(self):
        """T1.11: Rates that already have names are not re-generated."""
        _generate_name = self._get_generate_name()
        rates = [
            {
                "name": "My CPU rate",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "cost_type": "Infrastructure",
            },
            {"metric": {"name": "memory_gb_usage_per_hour"}, "cost_type": "Infrastructure"},
        ]
        used_names = set()
        results = []
        for rate in rates:
            if rate.get("name"):
                used_names.add(rate["name"])
                results.append(rate["name"])
            else:
                name = _generate_name(rate, used_names)
                used_names.add(name)
                results.append(name)

        self.assertEqual(results[0], "My CPU rate")
        self.assertTrue(len(results[1]) > 0)
        self.assertNotEqual(results[1], "My CPU rate")
