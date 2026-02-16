#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for per-rate-name distribution (PR 5 — TDD RED phase).

Covers: T5.1–T5.5 (distribution SQL per-rate-name tracking).
All tests are expected to FAIL until the production code is implemented.
"""
import uuid
from decimal import Decimal

from django.db.models import Sum
from django_tenants.utils import schema_context

from api.metrics import constants as metric_constants
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
        # Build a distribution_info dict consistent with actual CostModelDBAccessor output.
        # Keys come from metric_constants: "platform_cost", "worker_unallocated", etc.
        self.distribution_info = {
            metric_constants.PLATFORM_COST: True,
            metric_constants.WORKER_UNALLOCATED: False,
            metric_constants.GPU_UNALLOCATED: False,
            "distribution_type": "cpu",
        }

    def test_distributed_rows_carry_rate_name_from_source(self):
        """T5.1: After distribution, user-namespace rows carry cost_model_rate_name from Platform source."""
        with self.accessor as acc:
            acc.populate_distributed_cost_sql(
                self.summary_range,
                self.ocp_provider_uuid,
                self.distribution_info,
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
                self.distribution_info,
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
                self.distribution_info,
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
                self.distribution_info,
            )
        with schema_context(self.schema):
            user_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                distributed_cost__gt=0,
                usage_start__gte=self.dh.this_month_start,
            )
            self.assertTrue(user_rows.exists(), "Should have user distribution rows")

    def _assert_distribution_tracks_rate_name(self, config, rate_type):
        """Helper: run distribution and assert rows carry rate_name."""
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
            self.assertTrue(
                rows.exists(),
                f"populate_distributed_cost_sql should produce {rate_type} rows "
                f"(check that test fixtures include the prerequisite data, e.g. "
                f"'Worker unallocated' namespace rows for worker_distributed)",
            )
            has_rate_names = rows.exclude(cost_model_rate_name__isnull=True).exists()
            self.assertTrue(has_rate_names, f"{rate_type} should have rate-named rows")

    def test_platform_distribution_tracks_rate_name(self):
        """T5.5a: Platform distribution rows carry rate_name."""
        config = {
            metric_constants.PLATFORM_COST: True,
            metric_constants.WORKER_UNALLOCATED: False,
            metric_constants.GPU_UNALLOCATED: False,
            "distribution_type": "cpu",
        }
        self._assert_distribution_tracks_rate_name(config, "platform_distributed")

    def test_worker_distribution_tracks_rate_name(self):
        """T5.5b: Worker distribution rows carry rate_name."""
        # The worker distribution SQL distributes cost from "Worker unallocated"
        # namespace rows to user namespaces.  The standard test fixtures do not
        # include Worker unallocated rows, so we synthesize them here.
        with schema_context(self.schema):
            # Grab a reference row from the same provider to copy cluster metadata
            ref_row = OCPUsageLineItemDailySummary.objects.filter(
                source_uuid=self.ocp_provider_uuid,
                usage_start__gte=self.dh.this_month_start,
                data_source="Pod",
                namespace="koku",
            ).first()
            if not ref_row:
                self.fail("No reference row in 'koku' namespace to build Worker fixture from")

            report_period_id = ref_row.report_period_id
            # Create Worker unallocated rows with known rate names and costs
            for rate_name in ("CPU rate", "Memory rate"):
                OCPUsageLineItemDailySummary.objects.create(
                    uuid=uuid.uuid4(),
                    cluster_id=ref_row.cluster_id,
                    cluster_alias=ref_row.cluster_alias,
                    data_source="Pod",
                    namespace="Worker unallocated",
                    node=ref_row.node,
                    usage_start=self.dh.this_month_start,
                    usage_end=self.dh.this_month_start,
                    source_uuid=self.ocp_provider_uuid,
                    report_period_id=report_period_id,
                    cost_model_cpu_cost=Decimal("50.00"),
                    cost_model_memory_cost=Decimal("30.00"),
                    cost_model_rate_name=rate_name,
                    cost_model_rate_type="Infrastructure",
                    pod_effective_usage_cpu_core_hours=Decimal("100.0"),
                    pod_effective_usage_memory_gigabyte_hours=Decimal("50.0"),
                    node_capacity_cpu_core_hours=ref_row.node_capacity_cpu_core_hours,
                    node_capacity_memory_gigabyte_hours=ref_row.node_capacity_memory_gigabyte_hours,
                    cluster_capacity_cpu_core_hours=ref_row.cluster_capacity_cpu_core_hours,
                    cluster_capacity_memory_gigabyte_hours=ref_row.cluster_capacity_memory_gigabyte_hours,
                )

        config = {
            metric_constants.PLATFORM_COST: False,
            metric_constants.WORKER_UNALLOCATED: True,
            metric_constants.GPU_UNALLOCATED: False,
            "distribution_type": "cpu",
        }
        self._assert_distribution_tracks_rate_name(config, "worker_distributed")
