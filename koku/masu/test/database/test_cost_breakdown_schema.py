#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for cost_model_rate_name column on OCPUsageLineItemDailySummary (PR 2 — TDD RED phase).

All tests are expected to FAIL until the production code is implemented.
"""
from django_tenants.utils import schema_context
from model_bakery import baker

from masu.test import MasuTestCase
from reporting.models import OCPUsageLineItemDailySummary


class OCPLineItemRateNameTest(MasuTestCase):
    """T2.1–T2.3: Tests for cost_model_rate_name field on OCPUsageLineItemDailySummary."""

    def test_line_item_has_cost_model_rate_name_field(self):
        """T2.1: OCPUsageLineItemDailySummary has cost_model_rate_name field."""
        field = OCPUsageLineItemDailySummary._meta.get_field("cost_model_rate_name")
        self.assertIsNotNone(field)
        self.assertTrue(field.null)

    def test_line_item_rate_name_accepts_text(self):
        """T2.2: cost_model_rate_name can store text values."""
        with schema_context(self.schema):
            item = baker.make(
                OCPUsageLineItemDailySummary,
                cost_model_rate_name="CPU charge",
                usage_start=self.dh.this_month_start,
            )
            item.refresh_from_db()
            self.assertEqual(item.cost_model_rate_name, "CPU charge")

    def test_line_item_rate_name_nullable(self):
        """T2.3: cost_model_rate_name defaults to NULL."""
        with schema_context(self.schema):
            item = baker.make(
                OCPUsageLineItemDailySummary,
                usage_start=self.dh.this_month_start,
            )
            item.refresh_from_db()
            self.assertIsNone(item.cost_model_rate_name)
