# DD04: Cost Breakdown for Custom Costs — Detailed Design

**PRD Reference:** [PRD04 COST-2105 / COST-4415](README.md)
**Phase:** Phase 1 (COST-2105) with Phase 2 (COST-4415) notes
**Author:** Generated from codebase analysis
**Last updated:** 2026-02-16

---

## Table of Contents

1. [Overview](#1-overview)
2. [Scope and Boundaries](#2-scope-and-boundaries)
3. [Architecture Overview](#3-architecture-overview)
4. [PR Decomposition](#4-pr-decomposition)
5. [PR 1: Cost Model Rate Name Field](#5-pr-1-cost-model-rate-name-field)
6. [PR 2: Line Item Table Column](#6-pr-2-line-item-table-column)
7. [PR 3: Rate Name Threading Through Cost Application](#7-pr-3-rate-name-threading-through-cost-application)
8. [PR 4: Tiered Usage Rate Refactoring](#8-pr-4-tiered-usage-rate-refactoring)
9. [PR 5: Distribution SQL Per-Rate-Name Tracking](#9-pr-5-distribution-sql-per-rate-name-tracking)
10. [PR 6: Breakdown Summary Tables](#10-pr-6-breakdown-summary-tables)
11. [PR 7: API Layer — Provider Map, Query Handler, Serializers](#11-pr-7-api-layer--provider-map-query-handler-serializers)
12. [PR 8: Trino and Self-Hosted SQL Paths](#12-pr-8-trino-and-self-hosted-sql-paths)
13. [Cross-Cutting Concerns](#13-cross-cutting-concerns)
14. [Phase 2 Notes (COST-4415)](#14-phase-2-notes-cost-4415)
15. [Testing Strategy](#15-testing-strategy)
16. [Migration and Rollback Plan](#16-migration-and-rollback-plan)
17. [Open Questions and Decisions](#17-open-questions-and-decisions)

---

## 1. Overview

This document describes the technical design for the "Cost Breakdown for Custom Costs" feature. The goal is to break down aggregate cost categories (usage cost, overhead costs, markup) into their individual constituent rate names from the user's price list, so that the Sankey diagram and API responses show per-rate granularity instead of opaque aggregates.

**Phase 1** covers breaking down usage costs by price list rate names, plus proportional breakdown of overhead and markup by rate name. **Phase 2** adds cloud service breakdown (AmazonEC2, AmazonRDS, etc.) for raw cost and extends overhead/markup breakdown to include service-level constituents.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Rate attribution granularity | New `cost_model_rate_name` column on `OCPUsageLineItemDailySummary` | Follows existing `cost_model_rate_type` multiplicative-row pattern; enables standard GROUP BY |
| Summary table strategy | New parallel breakdown tables | Zero regression risk; existing tables, dashboards, CSVs, and forecasting untouched |
| Overhead breakdown | Per-rate distribution in SQL (split CTE: distribution + negation) | Accurate attribution required; naive single-CTE fails because cost model rows lack usage hours |
| Tiered rate refactoring | Per-rate execution of `usage_costs.sql` | Required to attribute each rate to its own `cost_model_rate_name` |
| Multiple rates per metric | Supported for all rate types (tiered, monthly, tag) via list-based rate structures + per-rate SQL execution | Real use case: users may define separate named rates for the same metric |
| Accessor refactoring | New parallel properties (no breaking change) | Existing `infrastructure_rates` etc. kept as-is; new `infrastructure_rates_by_name` added |
| Markup breakdown | Deferred to Phase 2 | Markup applies to infrastructure raw cost (cloud), which needs per-service granularity |
| GPU rate name | Covered in Phase 1 | GPU cost goes through `populate_tag_based_costs()` which already threads `metric_to_tag_params_map` |
| Rate `name` field | Mandatory from day one | Data migration auto-generates names for existing rates; API rejects requests without `name` |

---

## 2. Scope and Boundaries

### In Scope (Phase 1)

- New mandatory `name` field on cost model rates (API + data migration)
- `cost_model_rate_name` column on `OCPUsageLineItemDailySummary`
- Rate name threading through all cost application SQL (cloud PostgreSQL, Trino, self-hosted PostgreSQL)
- New breakdown summary tables for cluster, project, node, and VM perspectives
- API response extension with `breakdown` array on cost categories
- Pre-computed per-rate overhead breakdown via distribution SQL (not query-time approximation)
- Frontend: cost model editor `name` field, Sankey diagram per-rate nodes

### Out of Scope (Phase 1)

- Cloud service breakdown (`raw` cost by `product_code`/`service_name`) — Phase 2
- Markup breakdown by service — Phase 2
- Cost Explorer breakdown — not required per PRD
- CSV export of breakdown data — included in Phase 1 (flat column approach, see Section 13.7)
- Pure cloud provider views (AWS/Azure/GCP without OCP) — not required per PRD

### Environments

| Path | Engine | SQL directory | Affected |
|------|--------|---------------|----------|
| Cloud (console.redhat.com) | Trino + PostgreSQL | `trino_sql/` + `sql/` | Yes |
| On-prem (self-hosted) | PostgreSQL only | `self_hosted_sql/` | Yes |
| Cloud PostgreSQL summary | PostgreSQL | `sql/` | Yes |

---

## 3. Architecture Overview

### Data Flow (Current)

```
CostModel.rates JSON
    │
    ▼
CostModelDBAccessor          ← Extracts numeric values only
    │
    ▼
OCPCostModelCostUpdater       ← Orchestrates cost application
    │
    ├─► populate_usage_costs()     → usage_costs.sql (all rates at once)
    ├─► populate_tag_usage_costs() → infrastructure/supplementary_tag_rates.sql (per tag k:v)
    ├─► populate_monthly_cost_sql()→ monthly_cost_*.sql (per cost type)
    ├─► populate_vm_usage_costs()  → hourly_cost_virtual_machine.sql (per rate)
    └─► distribute_costs()         → distribute_*_cost.sql (per distribution type)
    │
    ▼
OCPUsageLineItemDailySummary   ← Rows with cost_model_rate_type
    │
    ▼
UI Summary Tables              ← GROUP BY cost_model_rate_type
    │
    ▼
API (ProviderMap → QueryHandler → _pack_data_object → Response)
```

### Data Flow (After Phase 1)

```
CostModel.rates JSON  (now includes "name" per rate)
    │
    ▼
CostModelDBAccessor          ← Extracts value + name per rate
    │
    ▼
OCPCostModelCostUpdater       ← Passes rate_name through all methods
    │
    ├─► populate_usage_costs_by_name() → usage_costs.sql (PER-RATE, not all-at-once)
    ├─► populate_tag_usage_costs()    → tag_rates.sql (per tag k:v, now with rate_name)
    ├─► populate_monthly_cost_sql()   → monthly_cost_*.sql (per cost type, now with rate_name)
    ├─► populate_vm_usage_costs()     → hourly_cost_*.sql (per rate, now with rate_name)
    └─► distribute_costs()            → distribute_*_cost.sql (PER-RATE-NAME distribution)
    │
    ▼
OCPUsageLineItemDailySummary   ← Rows with cost_model_rate_type + cost_model_rate_name
    │
    ├──────────────────────────────┐
    ▼                              ▼
Existing UI Summary Tables     NEW Breakdown Summary Tables
(UNCHANGED)                    (GROUP BY cost_model_rate_name)
    │                              │
    ▼                              ▼
API: aggregate cost fields     API: breakdown[] array
    │                              │
    └──────────┬───────────────────┘
               ▼
         API Response (existing fields + new breakdown)
```

---

## 4. PR Decomposition

The implementation is split into 8 PRs that can be reviewed and merged incrementally. Each PR is self-contained and does not break existing behavior.

| PR | Title | Dependencies | Risk |
|----|-------|--------------|------|
| PR 1 | Cost model rate `name` field | None | Low |
| PR 2 | `cost_model_rate_name` column on line item table | None | Low |
| PR 3 | Rate name threading (tag rates, monthly rates, VM rates, GPU rates) | PR 1, PR 2 | Medium |
| PR 4 | Tiered usage rate refactoring (per-rate execution + multiple rates per metric) | PR 1, PR 2 | High |
| PR 5 | Distribution SQL per-rate-name tracking | PR 3, PR 4 | High |
| PR 6 | Breakdown summary tables + population SQL | PR 2 | Medium |
| PR 7 | API layer (provider map, query handler, serializers) | PR 5, PR 6 | Medium |
| PR 8 | Trino and self-hosted SQL paths | PR 3, PR 4, PR 5 | Medium |

PRs 1 and 2 can be developed in parallel. PRs 3 and 4 can be developed in parallel after 1+2 merge. PR 5 depends on 3+4 (distribution SQL needs `cost_model_rate_name` populated on source rows). PR 6 can start as soon as PR 2 merges. PR 7 depends on both PR 5 and PR 6. PR 8 can be developed in parallel with PR 6/7.

---

## 5. PR 1: Cost Model Rate Name Field

### 5.1 Cost Model Serializer Changes

**File:** `koku/cost_models/serializers.py`

Add `name` field to `RateSerializer`:

```python
class RateSerializer(serializers.Serializer):
    """Serializer for the rate objects within a cost model."""

    name = serializers.CharField(max_length=50, required=True)
    metric = MetricSerializer(required=True)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    cost_type = serializers.ChoiceField(choices=metric_constants.COST_TYPE_CHOICES, required=True)
    tiered_rates = TieredRateSerializer(many=True, required=False)
    tag_rates = TagRateSerializer(required=False)
```

**`name` is mandatory from day one.** The data migration (Section 5.2) populates names for all existing rates before this code ships. The frontend must be updated to send `name` in the same release. There is no auto-generation fallback in the serializer — if `name` is missing, the API rejects the request.

**Uniqueness validation in `CostModelSerializer.validate()`:**

```python
def validate_rates(self, rates):
    """Validate rate names are unique within the cost model."""
    used_names = set()
    for rate in rates:
        name = rate.get("name", "").strip()
        if not name:
            raise serializers.ValidationError("Rate name is required.")
        if name in used_names:
            raise serializers.ValidationError(
                f"Rate names must be unique within a cost model. Duplicate: '{name}'"
            )
        used_names.add(name)
    return rates
```

The uniqueness validation happens at the serializer level since rates are stored as a JSONField on the `CostModel` model — there is no separate `Rate` model with a unique constraint.

### 5.2 Data Migration for Existing Rates

**File:** `koku/cost_models/migrations/NNNN_rate_name_migration.py`

This is a `RunPython` data migration that iterates over all `CostModel` objects and populates `name` for each rate in the `rates` JSON array. **Auto-generated names are only used here** — once the migration runs, all rates have names, and the API enforces `name` as mandatory from then on. Users can rename auto-generated names via the API/UI at any time.

```python
def populate_rate_names(apps, schema_editor):
    """Populate name field for existing rates."""
    CostModel = apps.get_model("cost_models", "CostModel")
    for cost_model in CostModel.objects.all():
        rates = cost_model.rates
        if not rates:
            continue
        used_names = set()
        for rate in rates:
            if rate.get("name"):
                used_names.add(rate["name"])
                continue
            # Generate name from description or metric
            name = _generate_name(rate, used_names)
            rate["name"] = name
            used_names.add(name)
        cost_model.rates = rates
        cost_model.save(update_fields=["rates"])
```

Name generation strategy (from PRD):

```python
def _generate_name(rate, used_names):
    """Generate a unique name for a rate."""
    description = rate.get("description", "")
    metric_name = rate.get("metric", {}).get("name", "unknown_metric")
    cost_type = rate.get("cost_type", "")

    if description:
        if len(description) <= 50:
            candidate = description
        else:
            candidate = description[:47] + "..."
    else:
        # No description: use metric + cost_type
        candidate = f"{metric_name}_{cost_type}".lower()[:47]

    # Ensure uniqueness within this cost model
    if candidate not in used_names:
        return candidate

    # Deduplicate with numeric suffix
    base = candidate[:44]
    for i in range(1000):
        deduped = f"{base}_{i:03d}"
        if deduped not in used_names:
            return deduped

    raise ValueError(f"Could not generate unique name for rate in cost model")
```

### 5.3 Cost Model API Response

The `name` field is already serialized by `RateSerializer`. The existing `GET /api/cost-management/v1/cost-models/{uuid}/` response will include `name` in each rate object:

```json
{
  "rates": [
    {
      "name": "JBoss subscription",
      "metric": {"name": "virtual_machine"},
      "cost_type": "Infrastructure",
      "description": "JBoss middleware license charge per core-month",
      "tiered_rates": [...],
      "tag_rates": {...}
    }
  ]
}
```

### 5.4 Files Changed

| File | Change |
|------|--------|
| `koku/cost_models/serializers.py` | Add `name` field to `RateSerializer`, uniqueness validation |
| `koku/cost_models/migrations/NNNN_*.py` | Data migration for existing rates |
| `koku/cost_models/test/test_serializers.py` | Tests for name validation, uniqueness, migration |

---

## 6. PR 2: Line Item Table Column

### 6.1 Model Change

**File:** `koku/reporting/provider/ocp/models.py`

Add `cost_model_rate_name` to `OCPUsageLineItemDailySummary`:

```python
class OCPUsageLineItemDailySummary(models.Model):
    # ... existing fields ...

    # Simplified Cost Model Cost terms
    cost_model_cpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_memory_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_volume_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_gpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_rate_type = models.TextField(null=True)
    cost_model_rate_name = models.TextField(null=True)  # NEW

    monthly_cost_type = models.TextField(null=True, choices=MONTHLY_COST_TYPES)
    # ...
```

The column is `TextField(null=True)` — nullable because:
- Existing rows won't have a name until re-processed
- Source data rows (raw cloud cost, `cost_model_rate_type IS NULL`) have no rate name
- Distribution rows are initially NULL; after PR 5, they carry `cost_model_rate_name` from the source cost (rate name for cost-model-attributed cost, NULL for cloud-sourced cost)

### 6.2 Index

Add an index for the new column since it will be used in GROUP BY for breakdown summary tables:

```python
indexes = [
    # ... existing indexes ...
    models.Index(fields=["cost_model_rate_name"], name="cost_model_rate_name_idx"),
]
```

### 6.3 Migration

**File:** `koku/reporting/migrations/0344_add_cost_model_rate_name.py`

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reporting", "0343_ocp_line_item_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocpusagelineitemdailysummary",
            name="cost_model_rate_name",
            field=models.TextField(null=True),
        ),
        migrations.AddIndex(
            model_name="ocpusagelineitemdailysummary",
            index=models.Index(
                fields=["cost_model_rate_name"],
                name="cost_model_rate_name_idx",
            ),
        ),
    ]
```

**Important:** Because `OCPUsageLineItemDailySummary` uses `PartitionInfo` with RANGE partitioning on `usage_start`, the `AddField` migration runs against the parent table and automatically applies to all partitions. This is standard PostgreSQL behavior for partitioned tables and requires no special handling. The `set_pg_extended_mode` pattern is only needed when creating new partitioned tables, not when adding columns.

### 6.4 Files Changed

| File | Change |
|------|--------|
| `koku/reporting/provider/ocp/models.py` | Add `cost_model_rate_name` field + index |
| `koku/reporting/migrations/0344_*.py` | AddField + AddIndex migration |

---

## 7. PR 3: Rate Name Threading Through Cost Application

This PR threads the rate name from `CostModelDBAccessor` through `OCPCostModelCostUpdater` to the SQL templates, for all code paths **except** tiered usage rates (handled in PR 4).

### 7.1 CostModelDBAccessor Changes

**File:** `koku/masu/database/cost_model_db_accessor.py`

#### 7.1.1 Design Principle: No Breaking Changes

Code triage reveals that `infrastructure_rates`, `supplementary_rates`, and all tag rate properties are consumed by:
- `ocp_cost_model_cost_updater.py` (production: `self._infra_rates.get(rate_term)`, `rates.get(metric, 0)`)
- `ocp_report_db_accessor.py` (production: `rates.get(metric, 0)` for SQL params, tag leaf values as scalars)
- 8+ test files with mocked scalar values

All callers expect **scalar** leaf values. Changing the existing properties would break every caller and every test.

**Approach: Add new parallel properties.** Existing properties (`infrastructure_rates`, `supplementary_rates`, etc.) remain unchanged. New properties provide the name alongside the value.

#### 7.1.2 New `infrastructure_rates_by_name` / `supplementary_rates_by_name`

These return a **list** structure (not a dict keyed by metric) to support multiple rates for the same metric:

```python
@property
def infrastructure_rates_by_name(self):
    """Return infrastructure rates with names, supporting multiple rates per metric.

    Captures both tiered usage rates and monthly rates (all use tiered_rates in JSON).

    Returns: [
        {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU charge"},
        {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.10"), "name": "Premium CPU"},
        {"metric": "memory_gb_usage_per_hour", "value": Decimal("0.03"), "name": "Memory charge"},
        {"metric": "node_cost_per_month", "value": Decimal("50.00"), "name": "Base node charge"},
        {"metric": "node_cost_per_month", "value": Decimal("30.00"), "name": "Premium node surcharge"},
    ]
    """
    rates = []
    for rate in self.cost_model.rates or []:
        cost_type = rate.get("cost_type")
        if cost_type != metric_constants.INFRASTRUCTURE_COST_TYPE:
            continue
        metric = rate.get("metric", {}).get("name")
        tiered_rates = rate.get("tiered_rates", [])
        if tiered_rates:
            rates.append({
                "metric": metric,
                "value": tiered_rates[0].get("value", 0),
                "name": rate.get("name", ""),
            })
    return rates

@property
def supplementary_rates_by_name(self):
    """Same as infrastructure_rates_by_name for supplementary cost type."""
    # Same logic with SUPPLEMENTARY_COST_TYPE
```

**Why a list, not a dict?** A user can define two separate rates both targeting `cpu_core_usage_per_hour` with different names (e.g., "Base CPU" at $0.05 and "Premium CPU surcharge" at $0.03). A dict keyed by metric would overwrite one — this is the **existing bug** in `price_list` which merges by metric name. The list preserves all rates.

#### 7.1.3 Multiple Rates Per Metric — Current Bug

Code triage confirms that `price_list` (line 52-80 in `cost_model_db_accessor.py`) builds `metric_rate_map` keyed by `metric_name`. When two rates share the same metric and cost_type, the second overwrites the first. The serializer has **no validation** preventing this.

**This is an existing data loss bug.** We fix it as part of this work by:
1. Using the new list-based properties (`infrastructure_rates_by_name`) which iterate over `cost_model.rates` directly instead of going through `price_list`
2. The per-rate execution in PR 4 naturally handles multiple rates per metric (one SQL execution per rate entry)

The legacy `infrastructure_rates` / `supplementary_rates` properties retain the existing (lossy) behavior for backward compatibility.

#### 7.1.4 `metric_to_tag_params_map` — Add `name`

**Current structure** (per entry):

```python
{
    "rate_type": "Infrastructure",
    "tag_key": "workload",
    "default_rate": "100.00",
    "value_rates": {"jboss": "40.00", "nginx": "20.00"},
}
```

**New structure** (add `name`):

```python
{
    "rate_type": "Infrastructure",
    "tag_key": "workload",
    "default_rate": "100.00",
    "value_rates": {"jboss": "40.00", "nginx": "20.00"},
    "name": "JBoss subscription",  # NEW
}
```

This is a **non-breaking** addition — existing callers that access `tag_params.get("rate_type")` or `tag_params.get("tag_key")` are unaffected. Only the new code reads `tag_params.get("name")`.

Implementation — add `"name": rate.get("name", "")` to each params dict in `metric_to_tag_params_map`.

#### 7.1.5 New `tag_rate_names` Property

For the `populate_tag_usage_costs()` and `populate_tag_usage_default_costs()` methods, which iterate over the tag rate dictionaries (not `metric_to_tag_params_map`), we need a way to look up the rate name by `(metric, tag_key)`:

```python
@property
def tag_rate_names(self):
    """Returns {metric: {tag_key: rate_name}} for tag-based usage rates."""
    names = defaultdict(dict)
    for rate in self.cost_model.rates or []:
        tag_rates = rate.get("tag_rates", {})
        if not tag_rates:
            continue
        metric = rate.get("metric", {}).get("name")
        tag_key = tag_rates.get("tag_key")
        names[metric][tag_key] = rate.get("name", "")
    return dict(names)
```

#### 7.1.6 Monthly Rates

The current `_update_monthly_cost()` method looks up rate values from `self._infra_rates` / `self._supplementary_rates` (dicts keyed by metric). This is lossy when multiple rates target the same metric — the second overwrites the first.

**Fix:** `_update_monthly_cost()` iterates directly over `infrastructure_rates_by_name` / `supplementary_rates_by_name` (the list-based properties), filtering by the metric names in `MONTHLY_COST_RATE_MAP`. This way, ALL rates for a given metric are applied, each with its own `cost_model_rate_name`. This is the same per-rate execution approach used for tiered usage rates in PR 4. No `_find_rate_name` helper is needed — the loop provides both the value and the name directly.

### 7.2 OCPCostModelCostUpdater Changes

**File:** `koku/masu/processor/ocp/ocp_cost_model_cost_updater.py`

#### 7.2.1 New Properties in `__init__`

```python
def __init__(self, schema, provider):
    # ... existing properties (unchanged) ...
    with CostModelDBAccessor(self._schema, self._provider_uuid) as cost_model_accessor:
        # Existing (unchanged):
        self._infra_rates = cost_model_accessor.infrastructure_rates
        self._supplementary_rates = cost_model_accessor.supplementary_rates
        self._tag_infra_rates = cost_model_accessor.tag_infrastructure_rates
        # ... etc ...

        # NEW — for rate name threading:
        self._infra_rates_by_name = cost_model_accessor.infrastructure_rates_by_name
        self._supplementary_rates_by_name = cost_model_accessor.supplementary_rates_by_name
        self._tag_rate_names = cost_model_accessor.tag_rate_names
```

#### 7.2.2 `_update_monthly_cost()`

Currently (lines 256-295) iterates over `MONTHLY_COST_RATE_MAP` and looks up a single rate value per metric from the dict. **Refactored** to iterate over the list-based `rates_by_name` properties, executing SQL once per rate entry. This correctly handles multiple rates for the same metric:

```python
def _update_monthly_cost(self, start_date, end_date):
    # Build a set of monthly metrics from MONTHLY_COST_RATE_MAP for filtering
    monthly_metrics = set(OCPUsageLineItemDailySummary.MONTHLY_COST_RATE_MAP.values())
    # Reverse map: metric -> cost_type (e.g., "node_cost_per_month" -> "Node")
    metric_to_cost_type = {v: k for k, v in OCPUsageLineItemDailySummary.MONTHLY_COST_RATE_MAP.items()}

    for rate_kind, rates_by_name in [
        ("Infrastructure", self._infra_rates_by_name),
        ("Supplementary", self._supplementary_rates_by_name),
    ]:
        for rate_entry in rates_by_name:
            metric = rate_entry["metric"]
            if metric not in monthly_metrics:
                continue
            rate = rate_entry["value"]
            name = rate_entry["name"]
            cost_type = metric_to_cost_type[metric]
            # ... existing amortization logic (prorate monthly rate by days) ...
            self._accessor.populate_monthly_cost_sql(
                cost_type, metric, amortized_rate, start_date, end_date,
                self._distribution, self._provider_uuid,
                rate_name=name,
            )
```

This ensures that if a user defines two `node_cost_per_month` Infrastructure rates with different names (e.g., "Base node charge" at $50 and "Premium node surcharge" at $30), both are applied as separate rows with distinct `cost_model_rate_name` values.

#### 7.2.3 `_update_monthly_tag_based_cost()`

This method (lines 297-353) already iterates per tag rate. The `name` is now available in `metric_to_tag_params_map`:

```python
def _update_monthly_tag_based_cost(self, start_date, end_date):
    for metric, tag_params_list in self.metric_to_tag_params_map.items():
        for tag_params in tag_params_list:
            name = tag_params.get("name")  # NEW — from updated metric_to_tag_params_map
            # ... existing case statement building ...
            self._accessor.populate_tag_cost_sql(
                cost_type, rate_type, tag_key, case_dict,
                start_date, end_date, self._distribution, self._provider_uuid,
                rate_name=name,  # NEW parameter
            )
```

#### 7.2.4 `_update_tag_usage_costs()` and `_update_tag_usage_default_costs()`

These pass `self._tag_infra_rates` / `self._tag_supplementary_rates` to `populate_tag_usage_costs()`. The tag rate dict structure (`{metric: {tag_key: {tag_value: rate}}}`) is **not changed** — we use the parallel `self._tag_rate_names` mapping instead:

```python
def _update_tag_usage_costs(self, start_date, end_date):
    self._accessor.populate_tag_usage_costs(
        self._tag_infra_rates, self._tag_supplementary_rates,
        start_date, end_date, self._cluster_id,
        tag_rate_names=self._tag_rate_names,  # NEW parameter
    )
```

Same pattern for `_update_tag_usage_default_costs()`.

### 7.3 OCPReportDBAccessor Changes

**File:** `koku/masu/database/ocp_report_db_accessor.py`

All cost population methods gain a `rate_name` keyword parameter (default `None`) and pass it to SQL params. This is a backward-compatible signature change — existing callers that don't pass `rate_name` get `NULL` in the database.

**Why `None` and not `""`:** The column is `TextField(null=True)`. Cloud-sourced costs naturally have `NULL`. PostgreSQL treats `NULL` and `''` as different groups in `GROUP BY`, so using `None` (→ SQL `NULL`) keeps cost-model rows without a name in the same group as cloud-sourced rows. The API aggregates all `NULL`-named entries as `{"name": "Cloud cost", "source": "cloud"}`. In the SQL templates, the parameter is passed as `%(rate_name)s`, and psycopg2 correctly translates Python `None` to SQL `NULL`.

#### 7.3.1 `populate_monthly_cost_sql()`

```python
def populate_monthly_cost_sql(self, cost_type, rate_type, rate, start_date, end_date,
                               distribution, provider_uuid, rate_name=None):
    # ... existing logic ...
    sql_params = {
        # ... existing params ...
        "rate_name": rate_name,  # NEW
    }
```

#### 7.3.2 `populate_tag_cost_sql()`

```python
def populate_tag_cost_sql(self, cost_type, rate_type, tag_key, case_dict, start_date, end_date,
                           distribution, provider_uuid, rate_name=None):
    sql_params = {
        # ... existing params ...
        "rate_name": rate_name,  # NEW
    }
```

#### 7.3.3 `populate_tag_usage_costs()` and `populate_tag_usage_default_costs()`

```python
def populate_tag_usage_costs(self, infrastructure_rates, supplementary_rates,
                              start_date, end_date, cluster_id, tag_rate_names=None):
    # ... inner loop per (metric, tag_key, tag_value) ...
    rate_name = (tag_rate_names or {}).get(metric, {}).get(tag_key)
    sql_params["rate_name"] = rate_name
```

#### 7.3.4 `populate_tag_based_costs()` (GPU and monthly tag rates)

This method (lines 1374-1466) handles GPU cost via `monthly_cost_gpu.sql` and monthly tag-based rates. It receives `metric_to_tag_params_map` which now includes `name`:

```python
def populate_tag_based_costs(self, start_date, end_date, provider_uuid,
                              metric_to_tag_params_map, cluster_params):
    # ... existing iteration over metric_to_tag_params_map ...
    for tag_params in param_list:
        rate_name = tag_params.get("name")  # NEW — from updated metric_to_tag_params_map
        sql_params["rate_name"] = rate_name
```

### 7.4 SQL Template Changes

All affected SQL files gain `cost_model_rate_name` in the INSERT column list and `{{rate_name}}` as the value.

#### 7.4.1 Tag Rate SQL (`infrastructure_tag_rates.sql`, `supplementary_tag_rates.sql`, defaults)

**Current INSERT columns:**

```sql
INSERT INTO {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary (
    uuid,
    -- ... existing columns ...
    cost_model_rate_type,
    -- ...
)
```

**New:**

```sql
INSERT INTO {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary (
    uuid,
    -- ... existing columns ...
    cost_model_rate_type,
    cost_model_rate_name,
    -- ...
)
-- In the SELECT:
    'Infrastructure' as cost_model_rate_type,
    {{rate_name}} as cost_model_rate_name,
```

This change applies to all four tag rate SQL files:
- `sql/openshift/cost_model/infrastructure_tag_rates.sql`
- `sql/openshift/cost_model/supplementary_tag_rates.sql`
- `sql/openshift/cost_model/default_infrastructure_tag_rates.sql`
- `sql/openshift/cost_model/default_supplementary_tag_rates.sql`

#### 7.4.2 Monthly Cost SQL (`monthly_cost_cluster_and_node.sql`, `monthly_cost_persistentvolumeclaim.sql`, `monthly_cost_virtual_machine.sql`)

Same pattern — add `cost_model_rate_name` to INSERT and `{{rate_name}}` to SELECT:

```sql
INSERT INTO {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary (
    -- ... existing columns ...
    cost_model_rate_type,
    cost_model_rate_name,
    -- ...
)
SELECT
    -- ...
    {{rate_type}} as cost_model_rate_type,
    {{rate_name}} as cost_model_rate_name,
    -- ...
```

#### 7.4.3 VM Rate SQL

Same pattern for:
- `trino_sql/openshift/cost_model/hourly_cost_virtual_machine.sql`
- `trino_sql/openshift/cost_model/hourly_vm_core.sql`
- `trino_sql/openshift/cost_model/monthly_vm_core.sql`

And their `self_hosted_sql/` equivalents.

#### 7.4.4 Node Cost by Tag SQL (`node_cost_by_tag.sql`, `monthly_cost_persistentvolumeclaim_by_tag.sql`)

Same pattern — these are already per-tag-key execution.

### 7.5 Files Changed

| File | Change |
|------|--------|
| `koku/masu/database/cost_model_db_accessor.py` | Extend rate structures to include `name`, add `tag_rate_names` property |
| `koku/masu/processor/ocp/ocp_cost_model_cost_updater.py` | Thread `rate_name` through all `_update_*` methods |
| `koku/masu/database/ocp_report_db_accessor.py` | Add `rate_name` parameter to all populate methods |
| `koku/masu/database/sql/openshift/cost_model/infrastructure_tag_rates.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/supplementary_tag_rates.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/default_infrastructure_tag_rates.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/default_supplementary_tag_rates.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/monthly_cost_cluster_and_node.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/monthly_cost_persistentvolumeclaim.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/monthly_cost_virtual_machine.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/node_cost_by_tag.sql` | Add `cost_model_rate_name` column |
| `koku/masu/database/sql/openshift/cost_model/monthly_cost_persistentvolumeclaim_by_tag.sql` | Add `cost_model_rate_name` column |

---

## 8. PR 4: Tiered Usage Rate Refactoring

This is the highest-complexity change. The current `usage_costs.sql` applies ALL tiered rates in a single INSERT, combining CPU, memory, and volume costs into one row per `(usage_start, cluster_id, node, namespace, data_source, persistentvolumeclaim, pod_labels, volume_labels, cost_category_id)`. This makes it impossible to attribute each rate to a separate `cost_model_rate_name`.

### 8.1 Current Behavior

`populate_usage_costs()` receives a flat dict of all rates:

```python
sql_params = {
    "cpu_core_usage_per_hour": rates.get("cpu_core_usage_per_hour", 0),
    "cpu_core_request_per_hour": rates.get("cpu_core_request_per_hour", 0),
    "cpu_core_effective_usage_per_hour": rates.get("cpu_core_effective_usage_per_hour", 0),
    "memory_gb_usage_per_hour": rates.get("memory_gb_usage_per_hour", 0),
    # ... 11 metrics total ...
}
```

The SQL computes `cost_model_cpu_cost` as the sum of all CPU-related rates, `cost_model_memory_cost` as the sum of all memory rates, etc. — all in one row.

### 8.2 New Behavior: Per-Rate Execution

The method signature changes to accept the new list-based rates structure from `infrastructure_rates_by_name` / `supplementary_rates_by_name`:

**Approach:**

1. Receive a list of `{"metric": str, "value": Decimal, "name": str}` rate entries
2. Execute `usage_costs.sql` once per rate entry, setting only that rate's metric to its value and all others to 0
3. Each execution produces rows tagged with `cost_model_rate_name`

```python
def populate_usage_costs_by_name(self, rate_type, rates_by_name, distribution,
                                   start_date, end_date, provider_uuid, report_period_id):
    """Update usage costs — per-rate execution supporting multiple rates per metric.

    Args:
        rates_by_name: list of {"metric": str, "value": Decimal, "name": str}
    """
    if not rates_by_name:
        self._delete_usage_cost_rows(provider_uuid, start_date, end_date,
                                      rate_type, report_period_id)
        return

    # Delete all existing usage cost rows for this rate_type first (once)
    self._delete_usage_cost_rows(provider_uuid, start_date, end_date,
                                  rate_type, report_period_id)

    # Execute SQL once per rate entry
    for rate_entry in rates_by_name:
        metric = rate_entry["metric"]
        value = rate_entry["value"]
        name = rate_entry["name"]

        if not value:
            continue

        sql_params = {
            "start_date": start_date,
            "end_date": end_date,
            "schema": self.schema,
            "source_uuid": provider_uuid,
            "report_period_id": report_period_id,
            "rate_type": rate_type,
            "distribution": distribution,
            "rate_name": name,
        }
        # Set all metrics to 0, then override the one for this rate
        for m in metric_constants.COST_MODEL_USAGE_RATES:
            sql_params[m] = 0
        sql_params[metric] = value

        sql = pkgutil.get_data("masu.database", "sql/openshift/cost_model/usage_costs.sql")
        sql = sql.decode("utf-8")
        self._prepare_and_execute_raw_sql_query(table_name, sql, sql_params, operation="INSERT")
```

This is a **new method** (`populate_usage_costs_by_name`), not a modification of the existing `populate_usage_costs`. The updater calls the new method when `rates_by_name` is available, falling back to the old method for backward compatibility during the transition.

**Multiple rates per metric example:** If a user defines "Base CPU" at $0.05 for `cpu_core_usage_per_hour` AND "Premium CPU" at $0.03 for `cpu_core_usage_per_hour`, we execute the SQL twice — once with `cpu_core_usage_per_hour = 0.05` (name "Base CPU") and once with `cpu_core_usage_per_hour = 0.03` (name "Premium CPU"). Each execution produces separate rows with distinct `cost_model_rate_name` values.

### 8.3 SQL Changes to `usage_costs.sql`

Add `cost_model_rate_name` to INSERT columns and SELECT:

```sql
INSERT INTO {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary (
    -- ... existing columns ...
    cost_model_rate_type,
    cost_model_rate_name,
    cost_model_cpu_cost,
    cost_model_memory_cost,
    cost_model_volume_cost,
    -- ...
)
-- ... CTEs unchanged ...
SELECT
    -- ... existing columns ...
    {{rate_type}} as cost_model_rate_type,
    {{rate_name}} as cost_model_rate_name,
    -- ... cost calculations unchanged ...
```

The cost calculation expressions in the SQL remain unchanged — they compute costs based on the provided rate parameters. Since we now pass only one rate's metrics (others set to 0), the cost columns will correctly reflect only that rate's contribution.

### 8.4 DELETE Logic

The current SQL has a DELETE at the top that removes existing rows for the rate_type. We must delete **all** rows for the rate_type **once** before the per-rate loop, not per iteration. Factor the delete into a separate step in `populate_usage_costs()`:

```python
# Delete once before the loop
self.delete_line_item_daily_summary_entries_for_date_range_raw(
    provider_uuid, start_date, end_date,
    table=OCPUsageLineItemDailySummary,
    filters={"cost_model_rate_type": rate_type, "report_period_id": report_period_id},
    null_filters={"monthly_cost_type": "IS NULL"},
)
```

The SQL template's leading DELETE statement should be removed or conditioned, since deletion is now handled externally.

### 8.5 `cluster_cost_per_hour` Special Case

`cluster_cost_per_hour` distributes costs across both CPU and memory columns based on the `distribution` setting. In per-rate execution, when `cluster_cost_per_hour` is the active rate (all others set to 0), the SQL still works correctly:
- The CTE `cte_node_cost` computes `node_cluster_hour_cost_cpu_per_day` and `node_cluster_hour_cost_mem_per_day` from the `cluster_cost_per_hour` parameter
- All other rate terms produce 0
- The result is a row with only cluster-hour costs attributed to this rate name

If a user has a cost model where `cluster_cost_per_hour` has a different rate name than their `cpu_core_usage_per_hour`, they naturally get separate rows — which is the desired behavior.

### 8.6 Performance Impact

For the common case (one CPU rate + one memory rate + one volume rate = 3 rate names), we execute the SQL 3 times instead of 1. Each execution:
- Reads the same base data (filtered by date range and source)
- Produces roughly the same number of rows

This is a ~3x increase in write volume to the line item table. Given that cost model application is already a batch job (not real-time), this is acceptable. The CTE `cte_node_cost` is re-computed each time; if performance is a concern, it can be materialized into a temp table once.

### 8.7 Multiple Rates for Same Metric (Fully Supported)

If a cost model has two rates with different names both targeting `cpu_core_usage_per_hour` (e.g., "Base CPU" at $0.05 and "Premium CPU surcharge" at $0.03), the per-rate execution produces two separate sets of rows in the line item table — each with its own `cost_model_rate_name`. This is correct and supported.

**Current bug:** The existing `price_list` property merges rates by metric name, so the second rate overwrites the first. The new `infrastructure_rates_by_name` property (list-based) preserves all rates. The old `infrastructure_rates` property retains the legacy lossy behavior but is no longer used by `populate_usage_costs_by_name`.

**API response:** Each rate appears as a separate entry in the `breakdown` array. If the user wants to see them combined, the frontend can aggregate by metric; but the API provides full granularity by rate name.

### 8.8 Files Changed

| File | Change |
|------|--------|
| `koku/masu/database/ocp_report_db_accessor.py` | Refactor `populate_usage_costs()` to per-rate loop |
| `koku/masu/database/sql/openshift/cost_model/usage_costs.sql` | Add `cost_model_rate_name`, remove leading DELETE |

---

## 9. PR 5: Distribution SQL Per-Rate-Name Tracking

### 9.1 Why Not Query-Time Approximation

The original design proposed computing overhead breakdown proportionally at query time, using the receiving project's cost composition to approximate the overhead source. This was rejected because:

- **Platform namespaces may have a completely different cost composition than user namespaces.** Platform might run zero JBoss workloads but heavy cloud infrastructure. A query-time proportional model would incorrectly attribute "JBoss subscription" overhead to a project that has JBoss costs but whose platform overhead is purely cloud-derived.
- **Accuracy is a hard requirement** for FinOps practitioners doing chargeback reporting.

### 9.2 Approach: Distribute Per `cost_model_rate_name` in SQL

After PRs 3-4 merge, all cost-model-attributed rows in `OCPUsageLineItemDailySummary` carry `cost_model_rate_name`. This means the source data (Platform namespaces, Worker unallocated, etc.) has per-rate-name granularity. The distribution SQL can leverage this.

**Key insight:** Instead of computing a single `platform_cost` scalar and distributing it, we compute `platform_cost` **per `cost_model_rate_name`** and distribute each rate name's contribution separately. This can be done in a **single SQL execution** by adding `cost_model_rate_name` to the CTEs and GROUP BYs.

### 9.3 Modified `distribute_platform_cost.sql`

#### 9.3.1 Why the Naive Approach Fails

A naive approach — adding `cost_model_rate_name` to the JOIN between `platform_cost` and `cte_narrow_dataset` — **does not work**. The fundamental issue:

- **Raw data rows** have `cost_model_rate_name = NULL` and `pod_effective_usage_cpu_core_hours > 0`
- **Cost model rows** (from `usage_costs.sql`) have `cost_model_rate_name = 'CPU rate'` etc. but `pod_effective_usage_cpu_core_hours = NULL` (usage hours are NOT in the INSERT column list of `usage_costs.sql`)

A JOIN on `cost_model_rate_name` would match user-namespace cost model rows (no usage hours) to platform cost model rows — producing `distributed_cost = 0` for every rate-named entry. Only NULL-named (cloud) cost would be distributed. This is wrong: "JBoss subscription" platform cost must be distributed to ALL user namespaces proportionally.

Conversely, removing the JOIN condition entirely breaks **Platform negation** — the `WHEN category_name = 'Platform'` case would over-negate by M× (once per platform rate name).

#### 9.3.2 Correct Approach: Split Distribution and Negation

The solution is to split into two CTEs with a UNION ALL:

1. **`cte_user_distribution`** — distributes each rate name's source cost to user namespaces proportionally by usage. Does NOT join on `cost_model_rate_name`. Usage comes from raw data rows (cost model rows contribute NULL/0).

2. **`cte_source_negation`** — negates each rate name's cost in the source namespace (Platform). Groups by `filtered.cost_model_rate_name` to negate per-rate.

**New `platform_cost` CTE (per rate name) — unchanged from before:**

```sql
platform_cost AS (
    SELECT SUM(
            COALESCE(infrastructure_raw_cost, 0) +
            COALESCE(infrastructure_markup_cost, 0) +
            COALESCE(cost_model_cpu_cost, 0) +
            COALESCE(cost_model_memory_cost, 0) +
            COALESCE(cost_model_volume_cost, 0)
        ) as platform_cost,
        filtered.usage_start,
        filtered.source_uuid,
        filtered.cluster_id,
        filtered.cost_model_rate_name   -- NEW: group by rate name
    FROM cte_narrow_dataset as filtered
    WHERE category_name = 'Platform'
    GROUP BY filtered.usage_start, filtered.cluster_id, filtered.source_uuid,
             filtered.cost_model_rate_name   -- NEW
)
```

**`user_defined_project_sum` — unchanged.** Usage proportions do not depend on rate name.

**New `cte_user_distribution` (replaces user-namespace portion of `cte_line_items`):**

```sql
cte_user_distribution as (
    SELECT
        max(report_period_id) as report_period_id,
        filtered.cluster_id,
        max(cluster_alias) as cluster_alias,
        filtered.data_source,
        filtered.usage_start,
        max(usage_end) as usage_end,
        filtered.namespace,
        filtered.node,
        max(resource_id) as resource_id,
        max(node_capacity_cpu_cores) as node_capacity_cpu_cores,
        max(node_capacity_cpu_core_hours) as node_capacity_cpu_core_hours,
        max(node_capacity_memory_gigabytes) as node_capacity_memory_gigabytes,
        max(node_capacity_memory_gigabyte_hours) as node_capacity_memory_gigabyte_hours,
        max(cluster_capacity_cpu_core_hours) as cluster_capacity_cpu_core_hours,
        max(cluster_capacity_memory_gigabyte_hours) as cluster_capacity_memory_gigabyte_hours,
        CASE WHEN {{distribution}} = 'cpu' THEN
            CASE WHEN max(udps.usage_cpu_sum) <= 0 THEN 0
            ELSE
                (sum(pod_effective_usage_cpu_core_hours) / max(udps.usage_cpu_sum))
                * max(pc.platform_cost)::decimal
            END
        WHEN {{distribution}} = 'memory' THEN
            CASE WHEN max(udps.usage_memory_sum) <= 0 THEN 0
            ELSE
                (sum(pod_effective_usage_memory_gigabyte_hours) / max(udps.usage_memory_sum))
                * max(pc.platform_cost)::decimal
            END
        END AS distributed_cost,
        pc.cost_model_rate_name,   -- from platform_cost, NOT from filtered
        max(cost_category_id) as cost_category_id
    FROM cte_narrow_dataset as filtered
    JOIN platform_cost as pc
        ON pc.usage_start = filtered.usage_start
        AND pc.cluster_id = filtered.cluster_id
        -- NO cost_model_rate_name condition: each user row joins to ALL rate names
    JOIN user_defined_project_sum as udps
        ON udps.usage_start = filtered.usage_start
        AND udps.cluster_id = filtered.cluster_id
    WHERE filtered.namespace IS NOT NULL
        AND (cost_category_id IS NULL OR max(filtered.category_name) != 'Platform')
    GROUP BY filtered.usage_start, filtered.node, filtered.namespace,
             filtered.cluster_id, cost_category_id, filtered.data_source,
             pc.cost_model_rate_name   -- produces M rows per user entity
)
```

**Why this works:** `cte_narrow_dataset` includes both raw data rows and cost model rows. The GROUP BY on `pc.cost_model_rate_name` creates M groups (one per platform rate name). Within each group, the same filtered rows appear. `sum(pod_effective_usage_cpu_core_hours)` only picks up values from raw data rows (cost model rows have NULL for usage hours, contributing 0). `max(pc.platform_cost)` picks up this rate name's platform cost. Result: each user entity gets `(usage / total) × rate_name_platform_cost` — correct proportional distribution per rate name.

**New `cte_source_negation` (replaces Platform-namespace portion of `cte_line_items`):**

```sql
cte_source_negation as (
    SELECT
        max(report_period_id) as report_period_id,
        filtered.cluster_id,
        max(cluster_alias) as cluster_alias,
        filtered.data_source,
        filtered.usage_start,
        max(usage_end) as usage_end,
        filtered.namespace,
        filtered.node,
        max(resource_id) as resource_id,
        max(node_capacity_cpu_cores) as node_capacity_cpu_cores,
        max(node_capacity_cpu_core_hours) as node_capacity_cpu_core_hours,
        max(node_capacity_memory_gigabytes) as node_capacity_memory_gigabytes,
        max(node_capacity_memory_gigabyte_hours) as node_capacity_memory_gigabyte_hours,
        max(cluster_capacity_cpu_core_hours) as cluster_capacity_cpu_core_hours,
        max(cluster_capacity_memory_gigabyte_hours) as cluster_capacity_memory_gigabyte_hours,
        0 - SUM(
            COALESCE(infrastructure_raw_cost, 0) +
            COALESCE(infrastructure_markup_cost, 0) +
            COALESCE(cost_model_cpu_cost, 0) +
            COALESCE(cost_model_memory_cost, 0) +
            COALESCE(cost_model_volume_cost, 0)
        ) AS distributed_cost,
        filtered.cost_model_rate_name,   -- from filtered rows, per-rate negation
        max(cost_category_id) as cost_category_id
    FROM cte_narrow_dataset as filtered
    WHERE filtered.namespace IS NOT NULL
        AND category_name = 'Platform'
    GROUP BY filtered.usage_start, filtered.node, filtered.namespace,
             filtered.cluster_id, cost_category_id, filtered.data_source,
             filtered.cost_model_rate_name   -- one negative row per rate name
)
```

**Why this works:** Groups Platform rows by `filtered.cost_model_rate_name`. Raw data rows (rate_name NULL) produce a negative row for cloud cost. Cost model rows (rate_name 'CPU rate', 'Memory rate') produce negative rows for each rate's cost. The sum of all negation rows = total platform cost, matching the sum of all distribution rows.

**Modified INSERT (UNION ALL):**

```sql
INSERT INTO {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary (
    -- ... existing columns ...
    cost_model_rate_type,
    cost_model_rate_name,   -- NEW
    distributed_cost,
    cost_category_id
)
SELECT
    -- ...
    {{cost_model_rate_type}} as cost_model_rate_type,
    ctl.cost_model_rate_name,
    ctl.distributed_cost,
    ctl.cost_category_id
FROM cte_user_distribution as ctl
WHERE ctl.distributed_cost != 0

UNION ALL

SELECT
    -- ... same columns ...
    {{cost_model_rate_type}} as cost_model_rate_type,
    ctl.cost_model_rate_name,
    ctl.distributed_cost,
    ctl.cost_category_id
FROM cte_source_negation as ctl
WHERE ctl.distributed_cost != 0;
```

**Result:** Each distributed row carries the `cost_model_rate_name` of the source cost it originated from. A project receiving $59 of platform distributed cost might get:
- $21 with `cost_model_rate_name = NULL` (from infrastructure_raw_cost, i.e., AmazonEC2 — Phase 2)
- $7 with `cost_model_rate_name = 'JBoss subscription'`
- $7 with `cost_model_rate_name = NULL` (from AmazonRDS raw cost — Phase 2)
- $3 with `cost_model_rate_name = 'Quota charge'`
- $21 with `cost_model_rate_name = NULL` (from Red Hat OpenShift Service on AWS raw cost — Phase 2)

In Phase 1, cloud-sourced costs have `cost_model_rate_name = NULL`. The API aggregates these as `{"name": "Cloud cost", "source": "cloud"}`. In Phase 2, `product_code`/`service_name` fills the gap.

#### 9.3.3 Validation

The sum of all `cte_user_distribution` rows + all `cte_source_negation` rows should equal zero (costs are redistributed, not created or destroyed). This can be verified with:

```sql
SELECT SUM(distributed_cost)
FROM {{schema}}.reporting_ocpusagelineitem_daily_summary
WHERE cost_model_rate_type = 'platform_distributed'
  AND usage_start BETWEEN {{start_date}} AND {{end_date}}
  AND source_uuid = {{source_uuid}};
-- Expected: 0
```

### 9.4 `cte_narrow_dataset` Change

The `cte_narrow_dataset` CTE (shared base) must also SELECT `cost_model_rate_name`:

```sql
WITH cte_narrow_dataset as (
    SELECT
        -- ... existing columns ...
        lids.cost_model_rate_name,   -- NEW
        cat.name as category_name
    FROM {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary AS lids
    -- ... existing joins and filters ...
)
```

### 9.5 `user_defined_project_sum` — No Change

The `user_defined_project_sum` CTE computes CPU/memory usage totals for distribution ratios. This does NOT group by `cost_model_rate_name` — it remains a single ratio per `(usage_start, cluster_id)`. The usage share is the same regardless of which rate name is being distributed.

### 9.6 All Five Distribution Types

The same split-CTE pattern (`cte_user_distribution` + `cte_source_negation` + UNION ALL) applies to all five distribution SQL files:

| File | Source CTE | Source Namespace | Change |
|------|-----------|-----------------|--------|
| `distribute_platform_cost.sql` | `platform_cost` | `category_name = 'Platform'` | Split into distribution + negation CTEs |
| `distribute_worker_cost.sql` | `worker_cost` | `namespace = 'Worker unallocated'` | Same split pattern |
| `distribute_unattributed_storage_cost.sql` | `unattributed_storage_cost` | `namespace = 'Storage unattributed'` | Same split pattern |
| `distribute_unattributed_network_cost.sql` | `unattributed_network_cost` | `namespace = 'Network unattributed'` | Same split pattern |
| `distribute_unallocated_gpu_cost.sql` (Trino + self-hosted) | `gpu_cost` | GPU unallocated rows | Same split pattern |

Each file follows the same structure:
1. **Source cost CTE**: adds `cost_model_rate_name` to GROUP BY
2. **`cte_user_distribution`**: joins source cost to user rows WITHOUT `cost_model_rate_name` condition; GROUP BY includes `source_cost.cost_model_rate_name`
3. **`cte_source_negation`**: groups source-namespace rows by `filtered.cost_model_rate_name` for per-rate negation
4. **INSERT**: UNION ALL of distribution + negation, with `cost_model_rate_name` in column list

**Note on `cte_narrow_dataset` after PR 4:** After per-rate execution of `usage_costs.sql`, the line item table has more rows (one per rate instead of one per rate_type). `cte_narrow_dataset` picks up all of them (it has no filter on `cost_model_rate_type`). This does NOT affect correctness — cost model rows contribute NULL/0 to usage hour sums, and the GROUP BY absorbs the extra rows. However, it does increase the row count flowing through the CTEs by a factor proportional to the number of rates (typically 3-5). This is acceptable for a batch job.

### 9.7 Row Count Impact

Currently, distribution produces ~1 row per `(usage_start, node, namespace, data_source)` per distribution type. With per-rate-name distribution, this becomes ~1 row per `(usage_start, node, namespace, data_source, cost_model_rate_name)`.

For a cost model with 5 rates plus cloud costs (which are NULL-named in Phase 1), this is a ~6x increase in distribution rows. For 50 namespaces, 30 days, 5 distribution types: `50 × 30 × 5 × 6 = 45,000` additional rows/month. This is acceptable given monthly partitioning.

### 9.8 `delete_monthly_cost_model_rate_type.sql` — No Change

The delete SQL filters by `cost_model_rate_type` (e.g., `platform_distributed`). It deletes all rows for that rate type regardless of `cost_model_rate_name`, which is correct — the entire distribution is recomputed on each run.

### 9.9 Files Changed

| File | Change |
|------|--------|
| `koku/masu/database/sql/openshift/cost_model/distribute_cost/distribute_platform_cost.sql` | Per-rate-name distribution |
| `koku/masu/database/sql/openshift/cost_model/distribute_cost/distribute_worker_cost.sql` | Per-rate-name distribution |
| `koku/masu/database/sql/openshift/cost_model/distribute_cost/distribute_unattributed_storage_cost.sql` | Per-rate-name distribution |
| `koku/masu/database/sql/openshift/cost_model/distribute_cost/distribute_unattributed_network_cost.sql` | Per-rate-name distribution |
| `koku/masu/database/trino_sql/openshift/cost_model/distribute_cost/distribute_unallocated_gpu_cost.sql` | Per-rate-name distribution |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/distribute_cost/distribute_unallocated_gpu_cost.sql` | Per-rate-name distribution |

---

## 10. PR 6: Breakdown Summary Tables

### 10.1 New Django Models

**File:** `koku/reporting/provider/ocp/models.py`

Create four new partitioned summary tables that mirror the existing cost summary tables but add `cost_model_rate_name` as a GROUP BY dimension.

#### 10.1.1 `OCPCostBreakdownP`

```python
class OCPCostBreakdownP(models.Model):
    """Breakdown of costs by rate name for cluster-level view."""

    class PartitionInfo:
        partition_type = "RANGE"
        partition_cols = ["usage_start"]

    class Meta:
        db_table = "reporting_ocp_cost_breakdown_p"
        indexes = [
            models.Index(fields=["usage_start"], name="ocp_brkdwn_usage_start"),
            models.Index(fields=["cost_model_rate_name"], name="ocp_brkdwn_rate_name"),
        ]

    id = models.UUIDField(primary_key=True)
    cluster_id = models.TextField()
    cluster_alias = models.TextField(null=True)
    usage_start = models.DateField(null=False)
    usage_end = models.DateField(null=False)
    cost_model_cpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_memory_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_volume_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_gpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_rate_type = models.TextField(null=True)
    cost_model_rate_name = models.TextField(null=True)
    infrastructure_raw_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    infrastructure_markup_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    source_uuid = models.ForeignKey(
        "reporting.TenantAPIProvider", on_delete=models.CASCADE,
        unique=False, null=True, db_column="source_uuid"
    )
    cost_category = models.ForeignKey("OpenshiftCostCategory", on_delete=models.CASCADE, null=True)
    raw_currency = models.TextField(null=True)
    distributed_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
```

#### 10.1.2 `OCPCostBreakdownByProjectP`

Same as `OCPCostBreakdownP` plus `namespace` field:

```python
class OCPCostBreakdownByProjectP(models.Model):
    """Breakdown of costs by rate name for project-level view."""

    class PartitionInfo:
        partition_type = "RANGE"
        partition_cols = ["usage_start"]

    class Meta:
        db_table = "reporting_ocp_cost_breakdown_by_project_p"
        indexes = [
            models.Index(fields=["usage_start"], name="ocp_brkdwn_proj_usage_start"),
            models.Index(fields=["namespace"], name="ocp_brkdwn_proj_namespace"),
            models.Index(fields=["cost_model_rate_name"], name="ocp_brkdwn_proj_rate_name"),
        ]

    id = models.UUIDField(primary_key=True)
    cluster_id = models.TextField()
    cluster_alias = models.TextField(null=True)
    namespace = models.CharField(max_length=253, null=True)
    usage_start = models.DateField(null=False)
    usage_end = models.DateField(null=False)
    cost_model_cpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_memory_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_volume_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_gpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_rate_type = models.TextField(null=True)
    cost_model_rate_name = models.TextField(null=True)
    infrastructure_raw_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    infrastructure_markup_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    source_uuid = models.ForeignKey(
        "reporting.TenantAPIProvider", on_delete=models.CASCADE,
        unique=False, null=True, db_column="source_uuid"
    )
    cost_category = models.ForeignKey("OpenshiftCostCategory", on_delete=models.CASCADE, null=True)
    raw_currency = models.TextField(null=True)
    distributed_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
```

#### 10.1.3 `OCPCostBreakdownByNodeP`

Same as `OCPCostBreakdownP` plus `node` field:

```python
class OCPCostBreakdownByNodeP(models.Model):
    """Breakdown of costs by rate name for node-level view."""

    class PartitionInfo:
        partition_type = "RANGE"
        partition_cols = ["usage_start"]

    class Meta:
        db_table = "reporting_ocp_cost_breakdown_by_node_p"
        indexes = [
            models.Index(fields=["usage_start"], name="ocp_brkdwn_node_usage_start"),
            models.Index(fields=["node"], name="ocp_brkdwn_node_node"),
            models.Index(fields=["cost_model_rate_name"], name="ocp_brkdwn_node_rate_name"),
        ]

    id = models.UUIDField(primary_key=True)
    cluster_id = models.TextField()
    cluster_alias = models.TextField(null=True)
    node = models.CharField(max_length=253, null=True)
    usage_start = models.DateField(null=False)
    usage_end = models.DateField(null=False)
    cost_model_cpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_memory_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_volume_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_gpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_rate_type = models.TextField(null=True)
    cost_model_rate_name = models.TextField(null=True)
    infrastructure_raw_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    infrastructure_markup_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    source_uuid = models.ForeignKey(
        "reporting.TenantAPIProvider", on_delete=models.CASCADE,
        unique=False, null=True, db_column="source_uuid"
    )
    cost_category = models.ForeignKey("OpenshiftCostCategory", on_delete=models.CASCADE, null=True)
    raw_currency = models.TextField(null=True)
    distributed_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
```

#### 10.1.4 `OCPVMBreakdownP`

For OpenShift Virtualization VM breakdown:

```python
class OCPVMBreakdownP(models.Model):
    """Breakdown of VM costs by rate name."""

    class PartitionInfo:
        partition_type = "RANGE"
        partition_cols = ["usage_start"]

    class Meta:
        db_table = "reporting_ocp_vm_breakdown_p"
        indexes = [
            models.Index(fields=["usage_start"], name="ocp_vm_brkdwn_usage_start"),
            models.Index(fields=["cost_model_rate_name"], name="ocp_vm_brkdwn_rate_name"),
        ]

    id = models.UUIDField(primary_key=True)
    cluster_id = models.TextField()
    cluster_alias = models.TextField(null=True)
    namespace = models.CharField(max_length=253, null=True)
    node = models.CharField(max_length=253, null=True)
    vm_name = models.TextField(null=True)
    usage_start = models.DateField(null=False)
    usage_end = models.DateField(null=False)
    cost_model_cpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_memory_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_volume_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_gpu_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    cost_model_rate_type = models.TextField(null=True)
    cost_model_rate_name = models.TextField(null=True)
    infrastructure_raw_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    infrastructure_markup_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
    source_uuid = models.ForeignKey(
        "reporting.TenantAPIProvider", on_delete=models.CASCADE,
        unique=False, null=True, db_column="source_uuid"
    )
    raw_currency = models.TextField(null=True)
    distributed_cost = models.DecimalField(max_digits=33, decimal_places=15, null=True)
```

### 10.2 Migration

**File:** `koku/reporting/migrations/0345_create_breakdown_summary_tables.py`

This migration creates the four new partitioned tables using the `set_pg_extended_mode` pattern (consistent with how `OCPGpuSummaryP`, `OCPVirtualMachineSummaryP`, and others were created):

```python
from django.db import migrations, models
import django.db.models.deletion
from koku.database import set_pg_extended_mode


class Migration(migrations.Migration):
    dependencies = [
        ("reporting", "0344_add_cost_model_rate_name"),
    ]

    operations = [
        migrations.RunPython(code=set_pg_extended_mode),
        migrations.CreateModel(
            name="OCPCostBreakdownP",
            fields=[
                ("id", models.UUIDField(primary_key=True, serialize=False)),
                ("cluster_id", models.TextField()),
                ("cluster_alias", models.TextField(null=True)),
                ("usage_start", models.DateField()),
                ("usage_end", models.DateField()),
                ("cost_model_cpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_memory_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_volume_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_gpu_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("cost_model_rate_type", models.TextField(null=True)),
                ("cost_model_rate_name", models.TextField(null=True)),
                ("infrastructure_raw_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("infrastructure_markup_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                ("raw_currency", models.TextField(null=True)),
                ("distributed_cost", models.DecimalField(decimal_places=15, max_digits=33, null=True)),
                # ... ForeignKey fields ...
            ],
        ),
        # ... CreateModel for the other 3 tables ...
        # ... AddIndex operations ...
    ]
```

### 10.3 Population SQL

New SQL files that mirror the existing UI summary SQL but add `cost_model_rate_name` to the GROUP BY.

#### 10.3.1 `sql/openshift/ui_summary/reporting_ocp_cost_breakdown_p.sql`

```sql
DELETE FROM {{schema | sqlsafe}}.reporting_ocp_cost_breakdown_p
WHERE usage_start >= {{start_date}}::date
    AND usage_start <= {{end_date}}::date
    AND source_uuid = {{source_uuid}}
;

INSERT INTO {{schema | sqlsafe}}.reporting_ocp_cost_breakdown_p (
    id,
    cluster_id,
    cluster_alias,
    usage_start,
    usage_end,
    infrastructure_raw_cost,
    infrastructure_markup_cost,
    cost_model_cpu_cost,
    cost_model_memory_cost,
    cost_model_volume_cost,
    cost_model_gpu_cost,
    cost_model_rate_type,
    cost_model_rate_name,
    source_uuid,
    cost_category_id,
    raw_currency,
    distributed_cost
)
    SELECT uuid_generate_v4() as id,
        cluster_id,
        cluster_alias,
        usage_start as usage_start,
        usage_start as usage_end,
        sum(infrastructure_raw_cost) as infrastructure_raw_cost,
        sum(infrastructure_markup_cost) as infrastructure_markup_cost,
        sum(cost_model_cpu_cost) as cost_model_cpu_cost,
        sum(cost_model_memory_cost) as cost_model_memory_cost,
        sum(cost_model_volume_cost) as cost_model_volume_cost,
        sum(cost_model_gpu_cost) as cost_model_gpu_cost,
        cost_model_rate_type,
        cost_model_rate_name,
        {{source_uuid}}::uuid as source_uuid,
        max(cost_category_id) as cost_category_id,
        max(raw_currency) as raw_currency,
        sum(distributed_cost) as distributed_cost
    FROM {{schema | sqlsafe}}.reporting_ocpusagelineitem_daily_summary
    WHERE usage_start >= {{start_date}}::date
        AND usage_start <= {{end_date}}::date
        AND source_uuid = {{source_uuid}}
    GROUP BY usage_start, cluster_id, cluster_alias, cost_model_rate_type, cost_model_rate_name
;
```

#### 10.3.2 `sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_project_p.sql`

Same as above, but adds `namespace` to SELECT and GROUP BY:

```sql
-- GROUP BY usage_start, cluster_id, cluster_alias, namespace, cost_model_rate_type, cost_model_rate_name
```

#### 10.3.3 `sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_node_p.sql`

Same, adds `node` to SELECT and GROUP BY.

#### 10.3.4 `sql/openshift/ui_summary/reporting_ocp_vm_breakdown_p.sql`

Mirrors `reporting_ocp_vm_summary_p.sql` but adds `cost_model_rate_name` GROUP BY. Includes VM-specific fields (`vm_name` extracted from `all_labels ->> 'vm_kubevirt_io_name'`).

### 10.4 Population Integration

**File:** `koku/masu/database/ocp_report_db_accessor.py`

Add the new breakdown tables to the UI summary population flow:

```python
BREAKDOWN_SUMMARY_TABLES = [
    "reporting_ocp_cost_breakdown_p",
    "reporting_ocp_cost_breakdown_by_project_p",
    "reporting_ocp_cost_breakdown_by_node_p",
    "reporting_ocp_vm_breakdown_p",
]

def populate_ui_summary_tables(self, summary_range, source_uuid, tables=UI_SUMMARY_TABLES):
    """Populate our UI summary tables (formerly materialized views)."""
    # ... existing logic for UI_SUMMARY_TABLES ...

    # Also populate breakdown tables
    self._populate_breakdown_summary_tables(summary_range, source_uuid)

def _populate_breakdown_summary_tables(self, summary_range, source_uuid):
    """Populate breakdown summary tables for cost rate-name granularity."""
    sql_params = {
        "start_date": summary_range.start_date,
        "end_date": summary_range.end_date,
        "schema": self.schema,
        "source_uuid": source_uuid,
    }
    for table_name in BREAKDOWN_SUMMARY_TABLES:
        sql = pkgutil.get_data("masu.database", f"sql/openshift/ui_summary/{table_name}.sql")
        sql = sql.decode("utf-8")
        self._prepare_and_execute_raw_sql_query(table_name, sql, sql_params, operation="DELETE/INSERT")
```

### 10.5 Row Count Estimation

For a typical cost model with 5 rates and daily data over 1 month:

| Table | Existing rows/day | New rows/day | Multiplier |
|-------|-------------------|--------------|------------|
| `reporting_ocp_cost_summary_p` | ~5-10 (by rate_type) | — | unchanged |
| `reporting_ocp_cost_breakdown_p` | — | ~25-50 (by rate_type × rate_name) | new table |
| `reporting_ocp_cost_summary_by_project_p` | ~50-500 (by project × rate_type) | — | unchanged |
| `reporting_ocp_cost_breakdown_by_project_p` | — | ~250-2500 (by project × rate_type × rate_name) | new table |

These are acceptable sizes given monthly partitioning and source-scoped queries.

### 10.6 Files Changed

| File | Change |
|------|--------|
| `koku/reporting/provider/ocp/models.py` | 4 new model classes |
| `koku/reporting/migrations/0345_*.py` | CreateModel for 4 tables |
| `koku/masu/database/ocp_report_db_accessor.py` | Populate new tables in UI summary flow |
| `koku/masu/database/sql/openshift/ui_summary/reporting_ocp_cost_breakdown_p.sql` | New file |
| `koku/masu/database/sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_project_p.sql` | New file |
| `koku/masu/database/sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_node_p.sql` | New file |
| `koku/masu/database/sql/openshift/ui_summary/reporting_ocp_vm_breakdown_p.sql` | New file |

---

## 11. PR 7: API Layer — Provider Map, Query Handler, Serializers

### 11.1 ProviderMap Changes

**File:** `koku/api/report/ocp/provider_map.py`

#### 11.1.1 New Breakdown Annotations

Add a new report type or extend the existing `costs` / `costs_by_project` report types with a method to query breakdown data. The cleanest approach: add a helper that queries the breakdown table and returns a dict keyed by `(date, namespace/cluster/node, cost_model_rate_type)`.

Since the existing `ProviderMap` pattern works with a single `query_table`, and we need to query **two** tables (existing summary for aggregate + new breakdown for detail), the breakdown query is best handled as a **secondary query** in the query handler, not in the provider map annotations.

The existing code dynamically selects the query table via `self._mapper.views[report_type][report_group]` (see `queries.py` line 220). The breakdown table must also vary by group-by, since node group-by needs `OCPCostBreakdownByNodeP` (which has the `node` column), not `OCPCostBreakdownP` (which doesn't).

**Approach:** Add a parallel `breakdown_views` dict in the provider map, following the same `(group_by_tuple) → table` pattern as `self.views`:

```python
# In self.views — existing, unchanged:
self.views = {
    "costs": {
        "default": OCPCostSummaryP,
        ("node",): OCPCostSummaryByNodeP,
        ("cluster", "node"): OCPCostSummaryByNodeP,
    },
    "costs_by_project": {
        "default": OCPCostSummaryByProjectP,
        ("project",): OCPCostSummaryByProjectP,
        ("cluster", "project"): OCPCostSummaryByProjectP,
    },
    "virtual_machines": {
        "default": OCPVirtualMachineSummaryP,
    },
    # ...
}

# NEW — parallel breakdown_views:
self.breakdown_views = {
    "costs": {
        "default": OCPCostBreakdownP,
        ("node",): OCPCostBreakdownByNodeP,
        ("cluster", "node"): OCPCostBreakdownByNodeP,
    },
    "costs_by_project": {
        "default": OCPCostBreakdownByProjectP,
        ("project",): OCPCostBreakdownByProjectP,
        ("cluster", "project"): OCPCostBreakdownByProjectP,
    },
    "virtual_machines": {
        "default": OCPVMBreakdownP,
    },
}
```

The query handler resolves the breakdown table using the same group-by logic:

```python
def _get_breakdown_table(self):
    """Select the breakdown table based on report type and group-by, mirroring self.views."""
    report_type = self.query_parameters.get("report_type", "costs")
    report_group = self._get_group_by()  # same tuple used for self.views
    try:
        return self._mapper.breakdown_views[report_type][report_group]
    except KeyError:
        return self._mapper.breakdown_views.get(report_type, {}).get("default")
```

This ensures that:
- `costs` (no group-by or cluster group-by) → `OCPCostBreakdownP`
- `costs` with `group_by[node]=*` → `OCPCostBreakdownByNodeP`
- `costs_by_project` → `OCPCostBreakdownByProjectP`
- `virtual_machines` → `OCPVMBreakdownP`
- Tag group-by → falls back to `OCPUsageLineItemDailySummary` (handled separately in Section 11.4)

#### 11.1.2 Breakdown PACK_DEFINITIONS

The breakdown data needs its own pack definition to structure the response:

```python
# No special PACK_DEFINITIONS needed for breakdown — it's a flat list
# of {name, source, value, units} objects, not the nested structure
# used by PACK_DEFINITIONS.
```

The breakdown is structured differently from the aggregate cost data, so it bypasses the `_pack_data_object()` mechanism entirely.

### 11.2 Query Handler Changes

**File:** `koku/api/report/ocp/query_handler.py`

#### 11.2.1 Breakdown Query

Add a method to query the breakdown table and attach results to the response:

```python
def _get_breakdown_data(self, date_filter, group_filter=None):
    """Query the breakdown summary table for per-rate-name data."""
    breakdown_table = self._get_breakdown_table()
    if not breakdown_table:
        return {}

    queryset = breakdown_table.objects.filter(date_filter)
    if group_filter:
        queryset = queryset.filter(group_filter)

    # Aggregate by (cost_model_rate_type, cost_model_rate_name)
    breakdown = queryset.values(
        "cost_model_rate_type", "cost_model_rate_name"
    ).annotate(
        total_cost=Sum(
            Coalesce(F("cost_model_cpu_cost"), Value(0)) +
            Coalesce(F("cost_model_memory_cost"), Value(0)) +
            Coalesce(F("cost_model_volume_cost"), Value(0)) +
            Coalesce(F("cost_model_gpu_cost"), Value(0))
        ),
        total_raw_cost=Sum(Coalesce(F("infrastructure_raw_cost"), Value(0))),
        total_markup_cost=Sum(Coalesce(F("infrastructure_markup_cost"), Value(0))),
        total_distributed=Sum(Coalesce(F("distributed_cost"), Value(0))),
        currency=Max("raw_currency"),
    ).order_by("-total_cost")

    return breakdown


def _get_breakdown_data_for_tags(self, date_filter, tag_group_by, group_filter=None):
    """Query the line item table directly for tag group-by breakdown.

    Tag group-by queries already run against OCPUsageLineItemDailySummary
    (not a summary table), so the breakdown data is available with minimal
    additional overhead — same table, same filters, one more GROUP BY column.
    """
    from koku.reporting.provider.ocp.models import OCPUsageLineItemDailySummary

    queryset = OCPUsageLineItemDailySummary.objects.filter(date_filter)
    if group_filter:
        queryset = queryset.filter(group_filter)

    group_fields = list(tag_group_by) + ["cost_model_rate_type", "cost_model_rate_name"]
    breakdown = queryset.values(*group_fields).annotate(
        total_cost=Sum(
            Coalesce(F("cost_model_cpu_cost"), Value(0)) +
            Coalesce(F("cost_model_memory_cost"), Value(0)) +
            Coalesce(F("cost_model_volume_cost"), Value(0)) +
            Coalesce(F("cost_model_gpu_cost"), Value(0))
        ),
        total_raw_cost=Sum(Coalesce(F("infrastructure_raw_cost"), Value(0))),
        total_markup_cost=Sum(Coalesce(F("infrastructure_markup_cost"), Value(0))),
        total_distributed=Sum(Coalesce(F("distributed_cost"), Value(0))),
        currency=Max("raw_currency"),
    ).order_by("-total_cost")

    return breakdown
```

#### 11.2.2 Overhead Breakdown (From Pre-Computed Distribution Data)

With PR 5, distributed rows now carry `cost_model_rate_name` from the source. The breakdown summary tables (PR 6) aggregate these by `(cost_model_rate_type, cost_model_rate_name)`. This means overhead breakdown is pre-computed and available directly from the breakdown table — no query-time proportional computation needed.

Example: querying `OCPCostBreakdownByProjectP` with `cost_model_rate_type = 'platform_distributed'` returns rows like:
- `{cost_model_rate_name: 'JBoss subscription', distributed_cost: 7.00}`
- `{cost_model_rate_name: 'Quota charge', distributed_cost: 3.00}`
- `{cost_model_rate_name: NULL, distributed_cost: 49.00}` (cloud-sourced, Phase 2 will add service names)

The query handler simply reads these as breakdown entries:

```python
def _build_overhead_breakdown(self, breakdown_qs, overhead_rate_type):
    """Build breakdown for an overhead type from pre-computed distribution data."""
    breakdown = []
    cloud_cost_total = Decimal("0")
    currency = "USD"
    for entry in breakdown_qs.filter(cost_model_rate_type=overhead_rate_type):
        name = entry["cost_model_rate_name"]
        currency = entry.get("currency", "USD")
        if name:
            breakdown.append({
                "name": name,
                "source": "rate",
                "value": entry["total_distributed"],
                "units": currency,
            })
        else:
            # NULL-named entries are cloud-sourced cost (no rate attribution)
            cloud_cost_total += entry["total_distributed"] or Decimal("0")

    # Phase 1: aggregate all NULL-named (cloud) cost as a single placeholder
    # Phase 2: replace with per-service entries from product_code/service_name
    if cloud_cost_total:
        breakdown.append({
            "name": "Cloud cost",
            "source": "cloud",
            "value": cloud_cost_total,
            "units": currency,
        })

    return breakdown
```

#### 11.2.3 Response Assembly

Override `_format_query_response()` to attach breakdown data:

```python
def _format_query_response(self):
    output = super()._format_query_response()

    # Attach breakdown data to the total
    if output.get("total"):
        total = output["total"]
        cost = total.get("cost", {})

        # Get breakdown for the entire query (all dates, all entities)
        breakdown_qs = self._get_breakdown_data(self.query_filter)

        # Determine breakdown_limit from query parameters (None = no limit)
        breakdown_limit = self.parameters.get("breakdown_limit")

        # Usage breakdown: rate-attributed costs
        usage_breakdown = self._build_usage_breakdown(breakdown_qs)
        if usage_breakdown and "usage" in cost:
            cost["usage"]["breakdown"] = self._apply_breakdown_limit(usage_breakdown, breakdown_limit)

        # Overhead breakdown (from pre-computed distribution data)
        overhead_types = [
            ("platform_distributed", "platform_distributed"),
            ("worker_unallocated_distributed", "worker_distributed"),
            ("storage_unattributed_distributed", "unattributed_storage"),
            ("network_unattributed_distributed", "unattributed_network"),
            ("gpu_unallocated_distributed", "gpu_distributed"),
        ]
        for cost_key, rate_type in overhead_types:
            if cost_key in cost:
                oh_breakdown = self._build_overhead_breakdown(breakdown_qs, rate_type)
                if oh_breakdown:
                    cost[cost_key]["breakdown"] = self._apply_breakdown_limit(oh_breakdown, breakdown_limit)

    # Also attach breakdown to each data row (per date, per group-by value)
    # Apply the same breakdown_limit to per-row breakdown for consistency
    self._attach_breakdown_to_data_rows(output.get("data", []), breakdown_limit)

    return output

def _build_usage_breakdown(self, breakdown_qs):
    """Build the usage cost breakdown from the breakdown query."""
    breakdown = []
    for entry in breakdown_qs:
        if entry["cost_model_rate_type"] in ("Infrastructure", "Supplementary"):
            name = entry["cost_model_rate_name"]
            if name:
                breakdown.append({
                    "name": name,
                    "source": "rate",
                    "value": entry["total_cost"],
                    "units": entry.get("currency", "USD"),
                })
    return breakdown


def _apply_breakdown_limit(self, breakdown, limit):
    """Apply top-N limiting with 'Other' aggregation.

    If limit is None, return the full breakdown (default behavior).
    Otherwise, return the top N entries and aggregate the rest as 'Other'.
    """
    if limit is None or len(breakdown) <= limit:
        return breakdown

    # Sort by value descending (should already be sorted, but ensure)
    breakdown.sort(key=lambda x: x.get("value", 0) or 0, reverse=True)

    top_entries = breakdown[:limit]
    rest = breakdown[limit:]
    other_total = sum((e.get("value", 0) or 0) for e in rest)
    units = breakdown[0].get("units", "USD") if breakdown else "USD"

    if other_total:
        top_entries.append({
            "name": "Other",
            "source": "other",
            "value": other_total,
            "units": units,
        })
    return top_entries
```

#### 11.2.4 Per-Data-Row Breakdown

The breakdown should also appear on each row in the `data` array (each date's data, each group-by entity's data). This uses a **single pre-fetch query** to avoid N+1 problems.

**Concrete design:**

```python
def _attach_breakdown_to_data_rows(self, data, breakdown_limit=None):
    """Attach breakdown arrays to each data row.

    Strategy: single query, index by (date, group_by_value), attach in O(1) per row.
    The same breakdown_limit applied to the total breakdown is also applied here.
    """
    breakdown_table = self._get_breakdown_table()
    if not breakdown_table:
        return

    # Determine the group-by dimension for indexing
    group_by_field = self._get_group_by_field()  # e.g., "namespace", "node", "cluster_id"

    # Single query: fetch ALL breakdown data for the date range + filter
    breakdown_qs = breakdown_table.objects.filter(self.query_filter)

    # Add group_by_field to the values if present
    group_fields = ["usage_start", "cost_model_rate_type", "cost_model_rate_name"]
    if group_by_field:
        group_fields.append(group_by_field)

    raw_breakdown = breakdown_qs.values(*group_fields).annotate(
        total_cost=Sum(
            Coalesce(F("cost_model_cpu_cost"), Value(0)) +
            Coalesce(F("cost_model_memory_cost"), Value(0)) +
            Coalesce(F("cost_model_volume_cost"), Value(0)) +
            Coalesce(F("cost_model_gpu_cost"), Value(0))
        ),
        total_distributed=Sum(Coalesce(F("distributed_cost"), Value(0))),
        currency=Max("raw_currency"),
    )

    # Build index: (date_str, group_value) -> list of breakdown entries
    breakdown_index = defaultdict(list)
    for entry in raw_breakdown:
        date_key = str(entry["usage_start"])
        group_key = entry.get(group_by_field, "__all__") if group_by_field else "__all__"
        breakdown_index[(date_key, group_key)].append(entry)

    # Attach to each data row in O(1) lookup
    for date_entry in data:
        date_str = date_entry.get("date", "")
        rows = date_entry.get(self._group_by_key, [date_entry])
        for row in rows:
            group_value = row.get(group_by_field, "__all__") if group_by_field else "__all__"
            row_breakdown = breakdown_index.get((date_str, group_value), [])
            if row_breakdown:
                self._inject_breakdown_into_cost(row, row_breakdown, breakdown_limit)

def _inject_breakdown_into_cost(self, row, breakdown_entries, breakdown_limit=None):
    """Inject breakdown arrays into a row's cost structure.

    Applies the same breakdown_limit (top-N with "Other" aggregation) as the total.
    """
    cost = row.get("cost", {})

    # Usage breakdown
    usage_entries = [
        e for e in breakdown_entries
        if e["cost_model_rate_type"] in ("Infrastructure", "Supplementary")
        and e["cost_model_rate_name"]
    ]
    if usage_entries and "usage" in cost:
        usage_breakdown = [
            {"name": e["cost_model_rate_name"], "source": "rate",
             "value": e["total_cost"], "units": e.get("currency", "USD")}
            for e in sorted(usage_entries, key=lambda x: x["total_cost"], reverse=True)
        ]
        cost["usage"]["breakdown"] = self._apply_breakdown_limit(usage_breakdown, breakdown_limit)

    # Overhead breakdown (same Cloud cost aggregation as total)
    overhead_map = {
        "platform_distributed": "platform_distributed",
        "worker_distributed": "worker_unallocated_distributed",
        "unattributed_storage": "storage_unattributed_distributed",
        "unattributed_network": "network_unattributed_distributed",
        "gpu_distributed": "gpu_unallocated_distributed",
    }
    for rate_type, cost_key in overhead_map.items():
        type_entries = [e for e in breakdown_entries if e["cost_model_rate_type"] == rate_type]
        if type_entries and cost_key in cost:
            oh_breakdown = []
            cloud_total = Decimal("0")
            currency = "USD"
            for e in sorted(type_entries, key=lambda x: x["total_distributed"] or 0, reverse=True):
                currency = e.get("currency", "USD")
                if e["cost_model_rate_name"]:
                    oh_breakdown.append({
                        "name": e["cost_model_rate_name"], "source": "rate",
                        "value": e["total_distributed"], "units": currency,
                    })
                else:
                    cloud_total += e["total_distributed"] or Decimal("0")
            if cloud_total:
                oh_breakdown.append({
                    "name": "Cloud cost", "source": "cloud",
                    "value": cloud_total, "units": currency,
                })
            if oh_breakdown:
                cost[cost_key]["breakdown"] = self._apply_breakdown_limit(oh_breakdown, breakdown_limit)
```

**Performance:** Single query against the breakdown summary table (partitioned, indexed on `usage_start` and `cost_model_rate_name`). For 30 days × 50 projects × 5 rates × 6 rate_types = ~45,000 rows. With the index, this query completes in milliseconds. The in-memory index build and lookup are O(n) and O(1) respectively.

### 11.3 OCP-on-Cloud Views

Code triage confirms that OCP-on-cloud query handlers (`OCPAWSReportQueryHandler`, `OCPAzureReportQueryHandler`, `OCPGCPReportQueryHandler`, `OCPAllReportQueryHandler`) inherit from **cloud** handlers, not from `OCPReportQueryHandler`. They query cloud-specific summary tables (`OCPAWSCostSummaryByServiceP`, etc.) which do **not** contain `cost_model_cpu_cost`, `cost_model_memory_cost`, `cost_model_rate_type`, or `cost_model_rate_name`.

Cost model rates are applied exclusively to `reporting_ocpusagelineitem_daily_summary` (the OCP table). Cloud costs live in separate tables.

**Implication for Phase 1:** The OCP-on-cloud views show cloud cost totals from their own summary tables and would need to source breakdown data from the **OCP breakdown tables** for the cost-model-attributed portion. This requires the OCP-on-cloud query handlers to:

1. Query their own summary tables for aggregate cost fields (unchanged behavior)
2. Additionally query the OCP breakdown tables for the `breakdown` array on usage/overhead categories

This can be implemented as a mixin or base class method that both `OCPReportQueryHandler` and the OCP-on-cloud handlers can call:

```python
class BreakdownMixin:
    """Mixin for querying OCP breakdown tables, usable by both OCP and OCP-on-cloud handlers."""

    def _get_ocp_breakdown(self, source_uuid_filter, date_filter, group_filter=None):
        """Query OCP breakdown summary tables for rate-name granularity."""
        # Always queries the OCP breakdown tables, regardless of which handler is calling
        breakdown_table = self._get_ocp_breakdown_table()  # Returns OCPCostBreakdownByProjectP etc.
        return breakdown_table.objects.filter(source_uuid_filter, date_filter)
```

### 11.4 Tag Group-By Breakdown

Tag group-by queries (e.g., `group_by[tag:app]=*`) already run against `OCPUsageLineItemDailySummary` because the cost summary tables lack tag data. Since the line item table has `cost_model_rate_name` (after PR 2), the breakdown for tag group-by is a secondary query on the same table with `cost_model_rate_name` added to the GROUP BY alongside the tag value. No additional tables needed. Performance overhead is minimal — same filters, same partitions, one more grouping dimension.

### 11.5 `breakdown_limit` Query Parameter

Add an optional `breakdown_limit` parameter to the report serializers:

```python
breakdown_limit = serializers.IntegerField(required=False, min_value=1, max_value=100)
```

**JSON behavior:** When set, the `breakdown` array on each cost category is limited to the top N entries by value, with the remainder aggregated as `{"name": "Other", "source": "other", "value": X, "units": "USD"}`. Default: no limit (full breakdown).

**CSV behavior:** For CSV, `breakdown_limit` acts as a boolean trigger: if present (any value), CSV includes `cost_model_rate_name` as a flat column by switching to the breakdown summary table. Top-N limiting and "Other" aggregation do NOT apply to CSV — CSV always returns the full set of rate names. This is consistent with CSV's role as raw data export for spreadsheet analysis.

### 11.6 Serializer Changes

Add `breakdown_limit` to the report query parameter serializers (optional integer, min 1, max 100). No other serializer changes needed — the `breakdown` array is an additive extension to the JSON response. Existing serializers for `group_by`, `filter`, and `order_by` remain unchanged.

### 11.7 CSV Export with Breakdown

The CSV code path in `execute_query()` needs a small addition: when breakdown is requested, swap the query source to the breakdown summary table and add `cost_model_rate_name` to `.values()`.

```python
def execute_query(self):
    ...
    if self.is_csv_output:
        if self.parameters.get("breakdown_limit") is not None:
            # Use breakdown table — adds cost_model_rate_name as a dimension
            breakdown_table = self._get_breakdown_table()
            if breakdown_table:
                query_data = breakdown_table.objects.filter(self.query_filter)
                query_data = query_data.values(
                    *self.query_group_by, "cost_model_rate_name"
                ).annotate(**self.annotations)
                data = list(query_data)
            else:
                data = list(query_data)
        else:
            data = list(query_data)
    ...
```

This adds `cost_model_rate_name` as a flat column, expanding rows — one row per (date, group_by_value, rate_name). The rest of the CSV pipeline (renderer, pagination) works unchanged. When breakdown is not requested, CSV behavior is identical to today.

### 11.8 Files Changed

| File | Change |
|------|--------|
| `koku/api/report/ocp/provider_map.py` | Add breakdown table references |
| `koku/api/report/ocp/query_handler.py` | Breakdown query, response assembly, CSV breakdown, `BreakdownMixin` |
| `koku/api/report/ocp/serializers.py` | Add `breakdown_limit` query parameter |
| `koku/api/report/all/openshift/provider_map.py` | Add breakdown table references (OCP-All view) |
| `koku/api/report/all/openshift/query_handler.py` | Use `BreakdownMixin` for breakdown |
| `koku/api/report/aws/openshift/query_handler.py` | Use `BreakdownMixin` for OCP breakdown on AWS view |
| `koku/api/report/azure/openshift/query_handler.py` | Use `BreakdownMixin` for OCP breakdown on Azure view |
| `koku/api/report/gcp/openshift/query_handler.py` | Use `BreakdownMixin` for OCP breakdown on GCP view |

---

## 12. PR 8: Trino and Self-Hosted SQL Paths

### 12.1 Trino SQL Changes

**Directory:** `koku/masu/database/trino_sql/openshift/cost_model/`

All Trino cost model SQL files need the same `cost_model_rate_name` column addition:

| File | Change |
|------|--------|
| `hourly_cost_virtual_machine.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `hourly_vm_core.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_vm_core.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `hourly_cost_vm_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `hourly_vm_core_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_vm_core_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_project_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_cost_gpu.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `distribute_cost/distribute_unallocated_gpu_cost.sql` | Per-rate-name distribution (split CTE pattern) — **already handled in PR 5** |

Note: The GPU distribution SQL files (`distribute_cost/`) are listed here for completeness, but the actual changes are part of **PR 5** (Section 9), not PR 8. PR 8 only covers cost *application* SQL (rates, not distribution).

#### 12.1.1 Trino Parquet Schema

The `cost_model_rate_name` column must also be added to the Parquet schema for Trino to recognize it. This is handled by the Hive metastore schema — when the Django migration adds the column to PostgreSQL, the Trino table DDL must also be updated.

Check if Trino reads from the PostgreSQL summary tables or from S3 parquet files. Based on the codebase analysis, Trino queries run against Hive tables backed by S3 parquet data for line items, but summary tables are in PostgreSQL. The `usage_costs.sql` in the `trino_sql/` path writes results back to PostgreSQL (via `_execute_trino_multipart_sql_query` which writes to the line item table). So the column addition to PostgreSQL is sufficient for the Trino write path.

However, if Trino reads from `reporting_ocpusagelineitem_daily_summary` (which it doesn't — Trino reads from S3 parquet), this would need parquet schema changes. Since Trino cost model SQL writes TO PostgreSQL and reads FROM Hive/S3 parquet source data, no parquet schema changes are needed.

### 12.2 Self-Hosted SQL Changes

**Directory:** `koku/masu/database/self_hosted_sql/openshift/cost_model/`

The self-hosted directory contains a subset of cost model SQL files for the on-prem PostgreSQL-only path. Note that tiered usage rates (`usage_costs.sql`), standard tag rates, and standard monthly rates do **not** have self-hosted variants — they use the `sql/` versions directly. Only VM-specific and GPU-specific SQL files have self-hosted equivalents.

| File | Change |
|------|--------|
| `hourly_cost_virtual_machine.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `hourly_vm_core.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_vm_core.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `hourly_cost_vm_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `hourly_vm_core_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_vm_core_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_project_tag_based.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `monthly_cost_gpu.sql` | Add `cost_model_rate_name` to INSERT + SELECT |
| `distribute_cost/distribute_unallocated_gpu_cost.sql` | Per-rate-name distribution (split CTE pattern) — **already handled in PR 5** |

### 12.3 Verification

After changes, verify both paths produce the same `cost_model_rate_name` values by:
1. Running cost model application for a test provider on both cloud and self-hosted paths
2. Comparing the `cost_model_rate_name` column values in `reporting_ocpusagelineitem_daily_summary`

### 12.4 Files Changed

| File | Change |
|------|--------|
| `koku/masu/database/trino_sql/openshift/cost_model/hourly_cost_virtual_machine.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/trino_sql/openshift/cost_model/hourly_vm_core.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/trino_sql/openshift/cost_model/monthly_vm_core.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/trino_sql/openshift/cost_model/hourly_cost_vm_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/trino_sql/openshift/cost_model/hourly_vm_core_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/trino_sql/openshift/cost_model/monthly_vm_core_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/trino_sql/openshift/cost_model/monthly_project_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/trino_sql/openshift/cost_model/monthly_cost_gpu.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/hourly_cost_virtual_machine.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/hourly_vm_core.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/monthly_vm_core.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/hourly_cost_vm_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/hourly_vm_core_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/monthly_vm_core_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/monthly_project_tag_based.sql` | Add `cost_model_rate_name` |
| `koku/masu/database/self_hosted_sql/openshift/cost_model/monthly_cost_gpu.sql` | Add `cost_model_rate_name` |

---

## 13. Cross-Cutting Concerns

### 13.1 Tag Rates and `monthly_cost_type = 'Tag'`

Code triage confirms that existing UI summary SQL files do **not** filter on `monthly_cost_type`. Rows with `monthly_cost_type = 'Tag'` (tag-based usage rates), `monthly_cost_type = 'Node'`, `monthly_cost_type = 'Cluster'`, `monthly_cost_type IS NULL` (tiered usage rates) — all are included in current summary aggregation.

The new breakdown summary SQL follows the same pattern: GROUP BY `(cost_model_rate_type, cost_model_rate_name)` without filtering on `monthly_cost_type`. This correctly includes all cost-model-attributed rows, regardless of how they were applied (tiered, tag-based, or monthly).

### 13.2 Delete/Cleanup Logic

The `delete_monthly_cost.sql` and `delete_monthly_cost_model_rate_type.sql` files do **not** need changes for `cost_model_rate_name`. They delete by `cost_model_rate_type` and `monthly_cost_type`, which is sufficient for cleanup before re-insertion.

### 13.3 `_update_markup_cost()` — No Changes

Markup cost is computed via Django ORM annotations (`infrastructure_raw_cost * markup_percentage`), not via SQL template. It operates on existing rows. No `cost_model_rate_name` is set because markup is not a "rate" — it's a percentage applied to raw cost.

In Phase 1, markup breakdown in the API response is deferred (markup applies to cloud raw cost, which doesn't have per-service granularity until Phase 2). The API returns markup as a single aggregate, which is the current behavior.

### 13.4 GPU Cost Path (Verified)

Code triage confirms the GPU cost flow:

1. GPU cost is applied via `populate_tag_based_costs()` in `ocp_report_db_accessor.py`
2. It uses `metric_to_tag_params_map` (which now includes `name` per PR 3) and `monthly_cost_gpu.sql` (Trino and self-hosted paths)
3. It writes to `OCPUsageLineItemDailySummary` and sets `cost_model_rate_type`
4. It does NOT currently set `cost_model_rate_name`

**Required changes (covered in PRs 3 + 8):**
- `populate_tag_based_costs()` reads `tag_params.get("name")` (returns `None` if absent) and passes it to `monthly_cost_gpu.sql` as `{{rate_name}}` — this is already designed in PR 3
- `monthly_cost_gpu.sql` (both Trino and self-hosted) adds `cost_model_rate_name` to INSERT and SELECT — covered in PR 8
- `OCPGpuSummaryP` model does NOT need `cost_model_rate_name` because GPU breakdown goes through the new breakdown summary tables, not the existing GPU summary table
- GPU distribution (`distribute_unallocated_gpu_cost.sql`) gets per-rate-name tracking — covered in PR 5

### 13.5 Currency Handling

The `cost_model_rate_name` is a text field and does not affect currency handling. The `raw_currency` field on line items continues to work as before. Breakdown entries in the API response inherit the currency from the parent cost category.

### 13.6 Forecasting

Forecasting uses the existing summary tables. Since we're not modifying those tables, forecasting is unaffected. If forecasting needs per-rate granularity in the future, it can query the new breakdown tables.

### 13.7 CSV Export

CSV export uses a separate flat data path — the query handlers return raw ORM annotations directly to `PaginatedCSVRenderer`, bypassing the nested JSON structure entirely. The `breakdown` array added in `_format_query_response()` is never seen by the CSV renderer.

**CSV breakdown design:** When the user requests breakdown in CSV (e.g., via a query parameter or `Accept: text/csv` with `breakdown_limit` set), the CSV code path queries the **breakdown summary table** instead of the regular summary table and includes `cost_model_rate_name` as a flat column. Each CSV row becomes `(date, group_by_value, cost_model_rate_name)` with the same flat cost fields — no nested structures, no dynamic columns:

```
date, project, cost_model_rate_name, cost_total, cost_raw, cost_usage, cost_markup, cost_platform_distributed, ...
2024-01, myapp, CPU rate,           10.50,      0.00,    10.50,      0.00,        3.20, ...
2024-01, myapp, Memory rate,         5.25,      0.00,     5.25,      0.00,        1.60, ...
2024-01, myapp, Cloud cost,         49.00,     49.00,     0.00,      4.90,       15.20, ...
```

This is consistent with the existing CSV pattern: adding a disaggregation dimension adds a column and expands rows, just like every other group-by key. The implementation is straightforward:

1. In `execute_query()` CSV branch, check if breakdown is requested.
2. If yes, swap the query source to the breakdown summary table (same fields plus `cost_model_rate_name`).
3. Add `cost_model_rate_name` to `.values()` and the result dict.
4. The rest of the CSV pipeline (renderer, pagination) works unchanged.

### 13.8 RBAC / Permissions

The breakdown data follows the same permission model as the existing cost data. If a user can see a project's costs, they can see the per-rate breakdown. No additional RBAC rules needed.

---

## 14. Phase 2 Notes (COST-4415)

Phase 2 adds cloud service breakdown for raw cost. Here's a brief sketch of the required changes:

### 14.1 Raw Cost Breakdown

OCP-on-cloud summary tables already have `product_code` (AWS/GCP) or `service_name` (Azure). The breakdown table for Phase 2 needs to GROUP BY this field in addition to `cost_model_rate_name`.

**New column on breakdown tables:** `cloud_service_name` (TextField, nullable). For Phase 1 rows this is NULL; for Phase 2 it contains the service name.

Alternatively, extend the existing breakdown tables to include `product_code`/`service_name` as a GROUP BY dimension, since the OCP-on-cloud line items already carry this information.

### 14.2 Markup Breakdown

Markup = `infrastructure_raw_cost * markup_percentage`. Since raw cost will be per-service in Phase 2:

```sql
-- Per-service markup:
markup_per_service = raw_cost_per_service * markup_percentage
```

This can be computed at query time by multiplying each service's raw cost by the markup rate, or pre-computed in the breakdown summary table.

### 14.3 Overhead Service-Level Breakdown

With Phase 2, overhead breakdown includes both rate names (from Phase 1) and cloud services. The proportional computation at query time naturally extends:

```
entity_total = sum(rate_costs) + sum(service_costs)
rate_share = rate_cost / entity_total
service_share = service_cost / entity_total
overhead_breakdown_entry = overhead_total * share
```

### 14.4 API Response

The `breakdown` array gains `"source": "service"` entries alongside `"source": "rate"`:

```json
{
  "raw": {
    "value": 444.00,
    "breakdown": [
      {"name": "AmazonEC2", "source": "service", "value": 183.00, "units": "USD"},
      {"name": "Red Hat OpenShift Service on AWS", "source": "service", "value": 176.00, "units": "USD"}
    ]
  }
}
```

---

## 15. Testing Strategy

### 15.1 Unit Tests

| Component | Test Focus |
|-----------|------------|
| `RateSerializer` | Name validation, max length, uniqueness within cost model |
| `CostModelDBAccessor` | Rate name extraction from JSON, tag_rate_names property |
| Data migration | Name generation from description, truncation, deduplication |
| `populate_usage_costs()` | Per-rate execution, correct rate_name on inserted rows |
| `populate_monthly_cost_sql()` | Rate name parameter passed to SQL |
| `populate_tag_usage_costs()` | Rate name per tag iteration |
| Breakdown summary SQL | Correct GROUP BY including cost_model_rate_name |
| Query handler | Breakdown query, overhead proportional computation |

### 15.2 Integration Tests

| Scenario | Verification |
|----------|-------------|
| Cost model with 3 tiered rates (CPU, memory, volume) | 3 separate rows with distinct `cost_model_rate_name` in line item table |
| Cost model with tag-based rates | Tag cost rows carry `cost_model_rate_name` from parent rate |
| Cost model with monthly rates (node, cluster, PVC, VM) | Monthly cost rows carry `cost_model_rate_name` |
| Distribution after cost application | Distributed rows carry `cost_model_rate_name` from source (rate name for cost-model cost, NULL for cloud cost). Sum of distribution = 0 per rate_name. |
| Breakdown summary tables populated correctly | Row counts match expectation (rate_type × rate_name × entity) |
| API response includes `breakdown` on usage | Array of {name, source, value, units} entries |
| API response includes `breakdown` on overhead | Proportional computation matches manual calculation |
| On-prem path (self_hosted_sql) | Same behavior as cloud PostgreSQL path |
| Trino path (VM rates) | Rate name written correctly via Trino SQL |
| Existing API response unchanged | All existing fields/values identical |

### 15.3 Performance Tests

| Scenario | Metric | Threshold |
|----------|--------|-----------|
| Cost model application with 10 rates | Time to complete | < 2x current |
| Breakdown summary population | Time to complete | < 1.5x current UI summary |
| API query with breakdown | Response time | < 1.5x current |
| Line item table row count after cost application | Row increase | ~5-10x for cost-model rows |

---

## 16. Migration and Rollback Plan

### 16.1 Forward Migration

1. **PR 1** merges: Data migration populates `name` on existing rates. API accepts `name` on create/update.
2. **PR 2** merges: `cost_model_rate_name` column added (NULL, no data yet). No behavioral change.
3. **PRs 3-4** merge: Next cost model application writes `cost_model_rate_name`. Historical data remains NULL until re-processed.
4. **PR 5** merges: Distribution SQL produces per-rate-name distributed rows. Existing distribution rows are replaced on next run.
5. **PR 6** merges: Breakdown summary tables created. Populated on next cost model application cycle.
6. **PR 7** merges: API returns `breakdown` array. For projects not yet re-processed, `breakdown` is empty.
7. **PR 8** merges: Trino and self-hosted paths also write `cost_model_rate_name`.

### 16.2 Backfill Strategy

After all PRs merge, historical data needs re-processing to populate `cost_model_rate_name`.

**Existing mechanism (from code triage):** The Celery task `update_all_summary_tables` (in `koku/masu/processor/tasks.py`, lines 879-910) iterates over all providers via `Provider.objects.get_accounts()` and queues `update_summary_tables` for each. For OCP providers, `update_summary_tables` chains to `update_cost_model_costs`, which calls `OCPCostModelCostUpdater.update_summary_cost_model_costs()`.

**Recommended approach:**

1. Invoke `update_all_summary_tables` via Celery (it's an existing registered task)
2. This will queue cost model re-application for every OCP provider across all tenants
3. Each provider's cost model application will:
   - Rewrite all cost-model-attributed rows with `cost_model_rate_name`
   - Rerun distribution SQL with per-rate-name tracking
   - Repopulate both existing UI summary tables and new breakdown summary tables

**Alternative mechanisms (also available):**
- **Per-provider API:** `GET /api/cost-management/v1/update_cost_model_costs/?provider_uuid=X&schema=Y` — triggers for a single provider
- **Cost model save:** Updating a cost model (even a no-op save) triggers `update_cost_model_costs` for all associated providers
- **No new task needed** — the existing infrastructure handles this

**Timing:** For a deployment with ~1000 providers, this batch job may take several hours. It should be run during a maintenance window or off-peak period. Progress can be monitored via Celery flower or log aggregation.

### 16.3 Rollback

Each PR can be reverted independently:
- **PR 7 revert**: API stops returning `breakdown`. No data loss.
- **PR 6 revert**: Breakdown tables stop being populated. Can be dropped.
- **PR 5 revert**: Distribution reverts to single-scalar (no per-rate-name). Distributed rows lose `cost_model_rate_name` on next run.
- **PRs 3-4 revert**: Cost application stops writing `cost_model_rate_name`. Column remains but unused.
- **PR 2 revert**: Column migration revert (drop column). Data loss is acceptable since it's derived.
- **PR 1 revert**: Data migration reverse populates NULL names. API stops accepting `name`. Need to handle existing rates that now have names.

---

## 17. Decisions Log

All questions have been resolved. This section serves as a decision record.

| # | Question | Decision |
|---|----------|----------|
| 1 | Top-N limiting for breakdown entries | Configurable via `breakdown_limit` query parameter. Default: no limit (full breakdown). Frontend typically shows full breakdown. |
| 2 | Multiple tiered rates for same metric | Fully supported. List-based rate structures + per-rate SQL execution. Each rate gets its own rows and breakdown entry. |
| 3 | Infrastructure vs Supplementary in breakdown | Merge under rate name. Rate name is the user-facing concept. |
| 4 | CSV export of breakdown data | Included in Phase 1. CSV treats `cost_model_rate_name` as a flat column (additional disaggregation dimension), expanding rows. Consistent with existing group-by pattern. Queries breakdown summary table when breakdown is requested. See Section 13.7. |
| 5 | Breakdown for tag group-by view | Supported in Phase 1. Tag group-by queries already run against `OCPUsageLineItemDailySummary` (not a summary table), so breakdown is a secondary query on the same table with `cost_model_rate_name` added to GROUP BY. Minimal performance overhead. |
| 6 | GPU rate name attribution | Covered in Phase 1. GPU goes through `populate_tag_based_costs()` which reads `name` from `metric_to_tag_params_map`. SQL files updated in PR 8. |
| 7 | Breakdown summary table cleanup | Same lifecycle as existing UI summary tables. Populated in `populate_ui_summary_tables()`. |
| 8 | Overhead breakdown accuracy | Per-rate-name distribution in SQL (PR 5). No query-time approximation. Accurate attribution from source. |
| 9 | CostModelDBAccessor breaking change | No breaking change. New parallel properties (`infrastructure_rates_by_name`, `tag_rate_names`). Existing properties unchanged. |
| 10 | OCP-on-cloud breakdown sourcing | OCP-on-cloud handlers use `BreakdownMixin` to query OCP breakdown tables for cost-model-attributed breakdown. Cloud cost breakdown deferred to Phase 2. |
| 11 | Backfill mechanism | Use existing `update_all_summary_tables` Celery task. No new task needed. |
| 12 | `monthly_cost_type = 'Tag'` rows in breakdown | Included. Breakdown SQL does not filter on `monthly_cost_type`, consistent with existing UI summary SQL. |
| 13 | NULL-named distribution entries (Phase 1) | Aggregate all NULL-named entries (cloud cost) as a single `{"name": "Cloud cost", "source": "cloud", "value": X}` placeholder. Phase 2 replaces with per-service entries. |
| 14 | `name` field mandatory vs optional | Mandatory from day one. Data migration auto-generates names for existing rates before code ships. Frontend must be updated in the same release. |
| 15 | Cross-cost-model rate name collisions | Not possible — only one cost model can be applied per cluster. No disambiguation logic needed. |
| 16 | Distribution SQL JOIN on `cost_model_rate_name` | Split into two CTEs: `cte_user_distribution` (no rate_name JOIN, correct usage proportions) + `cte_source_negation` (GROUP BY `filtered.cost_model_rate_name`, correct per-rate negation). UNION ALL for INSERT. Naive single-CTE approach fails because cost model rows lack usage hours. |
| 17 | CSV breakdown semantics | `breakdown_limit` acts as boolean trigger for CSV — include `cost_model_rate_name` column, no top-N limiting. CSV always returns full data for spreadsheet analysis. |
| 18 | Multiple monthly rates per metric | Fully supported. `_update_monthly_cost()` iterates over `rates_by_name` lists (same approach as tiered rates in PR 4), executing SQL once per rate entry. All rates for the same metric are applied with distinct `cost_model_rate_name`. |
| 19 | Breakdown table selection by group-by | Use `self._mapper.breakdown_views` — a parallel dict to `self.views` that maps `(report_type, group_by_tuple) → breakdown_table`. Resolved via `_get_breakdown_table()` using the same group-by logic as existing table selection. This ensures node group-by gets `OCPCostBreakdownByNodeP` (which has the `node` column), not `OCPCostBreakdownP`. |
| 20 | `breakdown_limit` applied to per-row data | Yes. The same `breakdown_limit` (top-N with "Other" aggregation) is applied to both the total breakdown and each per-row breakdown, for consistency. Passed through `_attach_breakdown_to_data_rows()` → `_inject_breakdown_into_cost()`. |
| 21 | `rate_name` default: `None` vs `""` | Use `None` (→ SQL `NULL`), not `""` (→ SQL `''`). The column is `TextField(null=True)` and cloud-sourced costs naturally have `NULL`. PostgreSQL treats `NULL` and `''` as different `GROUP BY` buckets. Using `None` keeps all unnamed rows in one bucket. JinjaSql + psycopg2 correctly translates Python `None` to SQL `NULL`. |
