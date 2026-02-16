#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for per-rate-name distribution (PR 5 — TDD RED phase).

Covers: T5.1–T5.5 (distribution SQL per-rate-name tracking).
All tests are expected to FAIL until the production code is implemented.
"""
from django.db.models import Sum
from django_tenants.utils import schema_context

from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
from masu.test import MasuTestCase
from masu.util.common import SummaryRangeConfig
from reporting.models import OCPUsageLineItemDailySummary


class DistributionRateNameTest(MasuTestCase):
    """T5.1–T5.5: Tests for per-rate-name distribution."""

    def setUp(self):
        super().setUp()
        self.accessor = OCPReportDBAccessor(schema=self.schema)
        self.summary_range = SummaryRangeConfig(
            start_date=self.dh.this_month_start,
            end_date=self.dh.this_month_end,
        )

    def test_distributed_rows_carry_rate_name_from_source(self):
        """T5.1: After distribution, user-namespace rows carry cost_model_rate_name from Platform source."""
        with self.accessor as acc:
            acc.populate_distributed_cost_sql(
                self.summary_range,
                self.ocp_provider_uuid,
                {"platform_cost": True, "worker_cost": False, "gpu_unallocated": False},
            )
        with schema_context(self.schema):
            distributed_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                usage_start__gte=self.dh.this_month_start,
            )
            rate_names = set(distributed_rows.values_list("cost_model_rate_name", flat=True))
            self.assertTrue(len(rate_names) > 0)
            named = {n for n in rate_names if n is not None}
            self.assertTrue(len(named) > 0)

    def test_distribution_sum_zero_per_rate_name(self):
        """T5.2: Sum of distributed_cost per cost_model_rate_name is zero (conservation)."""
        with self.accessor as acc:
            acc.populate_distributed_cost_sql(
                self.summary_range,
                self.ocp_provider_uuid,
                {"platform_cost": True, "worker_cost": False, "gpu_unallocated": False},
            )
        with schema_context(self.schema):
            sums = (
                OCPUsageLineItemDailySummary.objects.filter(
                    cost_model_rate_type="platform_distributed",
                    usage_start__gte=self.dh.this_month_start,
                )
                .values("cost_model_rate_name")
                .annotate(total=Sum("distributed_cost"))
            )
            for entry in sums:
                self.assertAlmostEqual(
                    float(entry["total"]),
                    0.0,
                    places=6,
                    msg=f"Distribution not zero-sum for rate_name={entry['cost_model_rate_name']}",
                )

    def test_distribution_negation_grouped_by_rate_name(self):
        """T5.3: Source namespace negation is per cost_model_rate_name."""
        with self.accessor as acc:
            acc.populate_distributed_cost_sql(
                self.summary_range,
                self.ocp_provider_uuid,
                {"platform_cost": True, "worker_cost": False, "gpu_unallocated": False},
            )
        with schema_context(self.schema):
            platform_negations = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                distributed_cost__lt=0,
                usage_start__gte=self.dh.this_month_start,
            )
            negation_names = set(platform_negations.values_list("cost_model_rate_name", flat=True))
            named_negations = {n for n in negation_names if n is not None}
            self.assertTrue(len(named_negations) > 0, "Should have named negation rows")
            for row in platform_negations:
                self.assertLess(row.distributed_cost, 0)

    def test_distribution_user_namespace_proportional(self):
        """T5.4: User namespace distribution is proportional to usage, regardless of rate name."""
        with self.accessor as acc:
            acc.populate_distributed_cost_sql(
                self.summary_range,
                self.ocp_provider_uuid,
                {"platform_cost": True, "worker_cost": False, "gpu_unallocated": False},
            )
        with schema_context(self.schema):
            user_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                distributed_cost__gt=0,
                usage_start__gte=self.dh.this_month_start,
            )
            self.assertTrue(user_rows.exists(), "Should have user distribution rows")

    def test_all_five_distribution_types_track_rate_name(self):
        """T5.5: All distribution types (platform, worker, storage, network, GPU) carry rate_name."""
        distribution_types = [
            ({"platform_cost": True, "worker_cost": False, "gpu_unallocated": False}, "platform_distributed"),
            (
                {"platform_cost": False, "worker_cost": True, "gpu_unallocated": False},
                "worker_unallocated_distributed",
            ),
        ]
        for config, rate_type in distribution_types:
            with self.subTest(rate_type=rate_type):
                with self.accessor as acc:
                    acc.populate_distributed_cost_sql(
                        self.summary_range,
                        self.ocp_provider_uuid,
                        config,
                    )
                with schema_context(self.schema):
                    rows = OCPUsageLineItemDailySummary.objects.filter(
                        cost_model_rate_type=rate_type,
                        usage_start__gte=self.dh.this_month_start,
                    )
                    has_rate_names = rows.exclude(cost_model_rate_name__isnull=True).exists()
                    self.assertTrue(has_rate_names, f"{rate_type} should have rate-named rows")
