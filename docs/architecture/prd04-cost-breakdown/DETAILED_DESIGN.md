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
9. [PR 5: Breakdown Summary Tables](#9-pr-5-breakdown-summary-tables)
10. [PR 6: API Layer — Provider Map, Query Handler, Serializers](#10-pr-6-api-layer--provider-map-query-handler-serializers)
11. [PR 7: Trino and Self-Hosted SQL Paths](#11-pr-7-trino-and-self-hosted-sql-paths)
12. [Cross-Cutting Concerns](#12-cross-cutting-concerns)
13. [Phase 2 Notes (COST-4415)](#13-phase-2-notes-cost-4415)
14. [Testing Strategy](#14-testing-strategy)
15. [Migration and Rollback Plan](#15-migration-and-rollback-plan)
16. [Open Questions and Decisions](#16-open-questions-and-decisions)

---

## 1. Overview

This document describes the technical design for the "Cost Breakdown for Custom Costs" feature. The goal is to break down aggregate cost categories (usage cost, overhead costs, markup) into their individual constituent rate names from the user's price list, so that the Sankey diagram and API responses show per-rate granularity instead of opaque aggregates.

**Phase 1** covers breaking down usage costs by price list rate names, plus proportional breakdown of overhead and markup by rate name. **Phase 2** adds cloud service breakdown (AmazonEC2, AmazonRDS, etc.) for raw cost and extends overhead/markup breakdown to include service-level constituents.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Rate attribution granularity | New `cost_model_rate_name` column on `OCPUsageLineItemDailySummary` | Follows existing `cost_model_rate_type` multiplicative-row pattern; enables standard GROUP BY |
| Summary table strategy | New parallel breakdown tables | Zero regression risk; existing tables, dashboards, CSVs, and forecasting untouched |
| Overhead breakdown | Query-time proportional computation | Distribution SQL stays simple; no redundant data storage |
| Tiered rate refactoring | Per-rate execution of `usage_costs.sql` | Required to attribute each rate to its own `cost_model_rate_name` |
| Markup breakdown | Deferred to Phase 2 | Markup applies to infrastructure raw cost (cloud), which needs per-service granularity |

---

## 2. Scope and Boundaries

### In Scope (Phase 1)

- New mandatory `name` field on cost model rates (API + data migration)
- `cost_model_rate_name` column on `OCPUsageLineItemDailySummary`
- Rate name threading through all cost application SQL (cloud PostgreSQL, Trino, self-hosted PostgreSQL)
- New breakdown summary tables for cluster, project, node, and VM perspectives
- API response extension with `breakdown` array on cost categories
- Query-time computation of overhead proportional breakdown
- Frontend: cost model editor `name` field, Sankey diagram per-rate nodes

### Out of Scope (Phase 1)

- Cloud service breakdown (`raw` cost by `product_code`/`service_name`) — Phase 2
- Markup breakdown by service — Phase 2
- Cost Explorer breakdown — not required per PRD
- CSV export of breakdown data — follow-up
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
    ├─► populate_usage_costs()     → usage_costs.sql (PER-RATE, not all-at-once)
    ├─► populate_tag_usage_costs() → tag_rates.sql (per tag k:v, now with rate_name)
    ├─► populate_monthly_cost_sql()→ monthly_cost_*.sql (per cost type, now with rate_name)
    ├─► populate_vm_usage_costs()  → hourly_cost_*.sql (per rate, now with rate_name)
    └─► distribute_costs()         → distribute_*_cost.sql (UNCHANGED)
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

The implementation is split into 7 PRs that can be reviewed and merged incrementally. Each PR is self-contained and does not break existing behavior.

| PR | Title | Dependencies | Risk |
|----|-------|--------------|------|
| PR 1 | Cost model rate `name` field | None | Low |
| PR 2 | `cost_model_rate_name` column on line item table | None | Low |
| PR 3 | Rate name threading (tag rates, monthly rates, VM rates) | PR 1, PR 2 | Medium |
| PR 4 | Tiered usage rate refactoring (per-rate execution) | PR 1, PR 2 | High |
| PR 5 | Breakdown summary tables + population SQL | PR 2 | Medium |
| PR 6 | API layer (provider map, query handler, serializers) | PR 5 | Medium |
| PR 7 | Trino and self-hosted SQL paths | PR 3, PR 4 | Medium |

PRs 1 and 2 can be developed in parallel. PRs 3 and 4 can be developed in parallel after 1+2 merge. PR 5 can start as soon as PR 2 merges. PR 6 depends on PR 5. PR 7 can be developed in parallel with PR 5/6.

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

**Validation logic** in `CostModelSerializer.validate()`:

```python
def validate_rates(self, rates):
    """Validate rate names are unique within the cost model."""
    names = [r.get("name") for r in rates if r.get("name")]
    if len(names) != len(set(names)):
        duplicate = next(n for n in names if names.count(n) > 1)
        raise serializers.ValidationError(
            f"Rate names must be unique within a cost model. Duplicate: '{duplicate}'"
        )
    return rates
```

The uniqueness validation happens at the serializer level since rates are stored as a JSONField on the `CostModel` model — there is no separate `Rate` model with a unique constraint.

### 5.2 Data Migration for Existing Rates

**File:** `koku/cost_models/migrations/NNNN_rate_name_migration.py`

This is a `RunPython` data migration that iterates over all `CostModel` objects and populates `name` for each rate in the `rates` JSON array:

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
- Distribution rows (`platform_distributed`, `worker_distributed`, etc.) intentionally have no rate name
- Source data rows (raw cloud cost, `cost_model_rate_type IS NULL`) have no rate name

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

#### 7.1.1 `infrastructure_rates` / `supplementary_rates`

**Current structure:**

```python
# Returns: {"cpu_core_usage_per_hour": Decimal("0.05"), "memory_gb_usage_per_hour": Decimal("0.03")}
```

**New structure:**

```python
# Returns: {
#   "cpu_core_usage_per_hour": {"value": Decimal("0.05"), "name": "CPU charge"},
#   "memory_gb_usage_per_hour": {"value": Decimal("0.03"), "name": "Memory charge"}
# }
```

Implementation — modify `get_rates()` method:

```python
def get_rates(self, value):
    """Get the rates, now including rate name."""
    rates = {}
    for rate in self.price_list:
        metric = rate.get("metric", {}).get("name")
        cost_type = rate.get("cost_type")
        if cost_type == value:
            tiered_rates = rate.get("tiered_rates", [])
            if tiered_rates:
                # Take the first tier's value (existing behavior)
                rate_value = tiered_rates[0].get("value", 0)
                rates[metric] = {
                    "value": rate_value,
                    "name": rate.get("name", ""),
                }
    return rates
```

#### 7.1.2 `metric_to_tag_params_map`

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

Implementation — in the `metric_to_tag_params_map` property, extract `name` from the rate object:

```python
@property
def metric_to_tag_params_map(self):
    """Returns the tag rate parameters, now including rate name."""
    metric_map = defaultdict(list)
    for rate in self.price_list:
        tag_rates = rate.get("tag_rates", {})
        if not tag_rates:
            continue
        metric = rate.get("metric", {}).get("name")
        tag_key = tag_rates.get("tag_key")
        tag_values = tag_rates.get("tag_values", [])
        # ... existing logic to build value_rates and default_rate ...
        params = {
            "rate_type": rate.get("cost_type"),
            "tag_key": tag_key,
            "default_rate": default_rate,
            "value_rates": value_rates,
            "defined_keys": defined_keys,
            "name": rate.get("name", ""),  # NEW
        }
        metric_map[metric].append(params)
    return dict(metric_map)
```

#### 7.1.3 Monthly Rates

The `_update_monthly_cost()` method in `OCPCostModelCostUpdater` iterates over `MONTHLY_COST_RATE_MAP` to get the rate type (e.g., `"node_cost_per_month"`) and looks up the rate value from `self._infra_rates` or `self._supplementary_rates`. Now it also needs the rate name.

Since `infrastructure_rates` now returns `{"metric": {"value": X, "name": "..."}}`, the caller can extract the name directly.

#### 7.1.4 Backward Compatibility

All callers that currently access `rates[metric]` as a scalar value need updating. To minimize churn, we can provide a helper:

```python
def rate_value(rate_entry):
    """Extract numeric value from a rate entry (supports old scalar or new dict format)."""
    if isinstance(rate_entry, dict):
        return rate_entry.get("value", 0)
    return rate_entry  # Legacy scalar format

def rate_name(rate_entry):
    """Extract name from a rate entry."""
    if isinstance(rate_entry, dict):
        return rate_entry.get("name", "")
    return ""
```

### 7.2 OCPCostModelCostUpdater Changes

**File:** `koku/masu/processor/ocp/ocp_cost_model_cost_updater.py`

#### 7.2.1 `_update_monthly_cost()`

Currently (lines 256-295), this method iterates over `MONTHLY_COST_RATE_MAP`:

```python
def _update_monthly_cost(self, start_date, end_date):
    for cost_type, rate_type in OCPUsageLineItemDailySummary.MONTHLY_COST_RATE_MAP.items():
        for rate_kind, rates in [("Infrastructure", self._infra_rates), ("Supplementary", self._supplementary_rates)]:
            rate_entry = rates.get(rate_type)
            if rate_entry:
                rate = rate_value(rate_entry)
                name = rate_name(rate_entry)
                # ... amortization logic ...
                self._accessor.populate_monthly_cost_sql(
                    cost_type, rate_type, rate, start_date, end_date,
                    self._distribution, self._provider_uuid,
                    rate_name=name,  # NEW parameter
                )
```

#### 7.2.2 `_update_monthly_tag_based_cost()`

This method (lines 297-353) already iterates per tag rate. Add `rate_name` to the `populate_tag_cost_sql()` call:

```python
def _update_monthly_tag_based_cost(self, start_date, end_date):
    for metric, tag_params_list in self.metric_to_tag_params_map.items():
        for tag_params in tag_params_list:
            name = tag_params.get("name", "")
            # ... existing case statement building ...
            self._accessor.populate_tag_cost_sql(
                cost_type, rate_type, tag_key, case_dict,
                start_date, end_date, self._distribution, self._provider_uuid,
                rate_name=name,  # NEW parameter
            )
```

#### 7.2.3 `_update_tag_usage_costs()` and `_update_tag_usage_default_costs()`

Tag usage costs are handled in `populate_tag_usage_costs()` and `populate_tag_usage_default_costs()`. These methods iterate per `(metric, tag_key, tag_value)`. The rate name needs to be passed per iteration.

Currently, `tag_infrastructure_rates` and `tag_supplementary_rates` are structured as:

```python
{"cpu_core_usage_per_hour": {"app": {"far": "0.20", "manager": "100.00"}}}
```

The rate name is **not** in this structure — it's in the parent rate object. We need to extend the tag rate structures to include the name, or pass a separate mapping. The cleanest approach:

**New structure for tag rates (from `CostModelDBAccessor`):**

```python
# tag_infrastructure_rates:
{
    "cpu_core_usage_per_hour": {
        "app": {"far": "0.20", "manager": "100.00"},
        "__name__": "CPU tag rate"  # NEW: rate name keyed by reserved key
    }
}
```

However, this pollutes the tag key namespace. Better approach — create a parallel name mapping:

```python
# New property on CostModelDBAccessor:
@property
def tag_rate_names(self):
    """Returns {metric: {tag_key: rate_name}} for tag-based rates."""
    names = defaultdict(dict)
    for rate in self.price_list:
        tag_rates = rate.get("tag_rates", {})
        if not tag_rates:
            continue
        metric = rate.get("metric", {}).get("name")
        tag_key = tag_rates.get("tag_key")
        names[metric][tag_key] = rate.get("name", "")
    return dict(names)
```

Store in `OCPCostModelCostUpdater.__init__`:

```python
self._tag_rate_names = cost_model_accessor.tag_rate_names
```

Thread through `populate_tag_usage_costs()`:

```python
def populate_tag_usage_costs(self, infra_rates, supp_rates, start_date, end_date, cluster_id, tag_rate_names=None):
    # ... existing iteration ...
    for metric in rate:
        tags = rate.get(metric, {})
        for tag_key in tags:
            tag_vals = tags.get(tag_key, {})
            for val_name in tag_vals:
                rate_name = (tag_rate_names or {}).get(metric, {}).get(tag_key, "")
                sql_params["rate_name"] = rate_name  # NEW
                # ... execute SQL ...
```

### 7.3 OCPReportDBAccessor Changes

**File:** `koku/masu/database/ocp_report_db_accessor.py`

All cost population methods gain a `rate_name` parameter and pass it to SQL:

#### 7.3.1 `populate_monthly_cost_sql()`

```python
def populate_monthly_cost_sql(self, cost_type, rate_type, rate, start_date, end_date,
                               distribution, provider_uuid, rate_name=""):
    # ... existing logic ...
    sql_params = {
        # ... existing params ...
        "rate_name": rate_name,  # NEW
    }
```

#### 7.3.2 `populate_tag_cost_sql()`

```python
def populate_tag_cost_sql(self, cost_type, rate_type, tag_key, case_dict, start_date, end_date,
                           distribution, provider_uuid, rate_name=""):
    sql_params = {
        # ... existing params ...
        "rate_name": rate_name,  # NEW
    }
```

#### 7.3.3 `populate_tag_usage_costs()` and `populate_tag_usage_default_costs()`

```python
def populate_tag_usage_costs(self, infrastructure_rates, supplementary_rates,
                              start_date, end_date, cluster_id, tag_rate_names=None):
    # ... inner loop ...
    sql_params["rate_name"] = (tag_rate_names or {}).get(metric, {}).get(tag_key, "")
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

Group rates by their resource type and execute SQL once per rate:

```python
METRIC_RESOURCE_TYPE = {
    "cpu_core_usage_per_hour": "cpu",
    "cpu_core_request_per_hour": "cpu",
    "cpu_core_effective_usage_per_hour": "cpu",
    "memory_gb_usage_per_hour": "memory",
    "memory_gb_request_per_hour": "memory",
    "memory_gb_effective_usage_per_hour": "memory",
    "storage_gb_usage_per_month": "volume",
    "storage_gb_request_per_month": "volume",
    "node_core_cost_per_hour": "cpu",
    "cluster_core_cost_per_hour": "cpu",
    "cluster_cost_per_hour": "cpu_and_memory",  # Special: distributes across both
}
```

**Approach:**

1. Group the rates by their rate name (from `CostModelDBAccessor`)
2. For each rate name, build `sql_params` with only that rate's metric set to its value and all others set to 0
3. Execute `usage_costs.sql` once per rate name, setting `{{rate_name}}`

```python
def populate_usage_costs(self, rate_type, rates, distribution, start_date, end_date,
                          provider_uuid, report_period_id):
    """Update usage costs — now per-rate execution."""
    if not rates:
        # Delete existing and return (unchanged)
        self._delete_usage_cost_rows(...)
        return

    # Delete all existing usage cost rows for this rate_type first
    self._delete_usage_cost_rows(provider_uuid, start_date, end_date, rate_type, report_period_id)

    # Group metrics by rate name
    rates_by_name = defaultdict(dict)
    for metric, rate_info in rates.items():
        name = rate_name(rate_info)
        value = rate_value(rate_info)
        if value:
            rates_by_name[name][metric] = value

    # Execute SQL once per rate name
    for name, metric_values in rates_by_name.items():
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
        # Set all metrics to 0, then override the ones for this rate
        for metric in metric_constants.COST_MODEL_USAGE_RATES:
            sql_params[metric] = metric_values.get(metric, 0)

        sql = pkgutil.get_data("masu.database", "sql/openshift/cost_model/usage_costs.sql")
        sql = sql.decode("utf-8")
        self._prepare_and_execute_raw_sql_query(table_name, sql, sql_params, operation="INSERT")
```

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

`cluster_cost_per_hour` distributes costs across both CPU and memory columns based on the `distribution` setting. When it's the only rate in a group, it works naturally. When combined with other CPU or memory rates in the same rate name, the existing SQL handles it correctly because the CTE computation for `node_cluster_hour_cost_cpu_per_day` and `node_cluster_hour_cost_mem_per_day` is separate.

If a user has a cost model where `cluster_cost_per_hour` has a different rate name than their `cpu_core_usage_per_hour`, they'll naturally get separate rows — which is the desired behavior.

### 8.6 Performance Impact

For the common case (one CPU rate + one memory rate + one volume rate = 3 rate names), we execute the SQL 3 times instead of 1. Each execution:
- Reads the same base data (filtered by date range and source)
- Produces roughly the same number of rows

This is a ~3x increase in write volume to the line item table. Given that cost model application is already a batch job (not real-time), this is acceptable. The CTE `cte_node_cost` is re-computed each time; if performance is a concern, it can be materialized into a temp table once.

### 8.7 Edge Case: Multiple Rates for Same Metric

If a cost model has two rates with different names both targeting `cpu_core_usage_per_hour`, they would end up in different `rates_by_name` groups and produce separate rows. Per the PRD's open question #2, we aggregate per metric in Phase 1. However, with the new per-rate execution, this naturally separates them — which is actually more correct. The PRD recommendation to "aggregate per metric" applies to the API response aggregation, not the storage level.

### 8.8 Files Changed

| File | Change |
|------|--------|
| `koku/masu/database/ocp_report_db_accessor.py` | Refactor `populate_usage_costs()` to per-rate loop |
| `koku/masu/database/sql/openshift/cost_model/usage_costs.sql` | Add `cost_model_rate_name`, remove leading DELETE |

---

## 9. PR 5: Breakdown Summary Tables

### 9.1 New Django Models

**File:** `koku/reporting/provider/ocp/models.py`

Create four new partitioned summary tables that mirror the existing cost summary tables but add `cost_model_rate_name` as a GROUP BY dimension.

#### 9.1.1 `OCPCostBreakdownP`

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

#### 9.1.2 `OCPCostBreakdownByProjectP`

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

#### 9.1.3 `OCPCostBreakdownByNodeP`

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

#### 9.1.4 `OCPVMBreakdownP`

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

### 9.2 Migration

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

### 9.3 Population SQL

New SQL files that mirror the existing UI summary SQL but add `cost_model_rate_name` to the GROUP BY.

#### 9.3.1 `sql/openshift/ui_summary/reporting_ocp_cost_breakdown_p.sql`

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

#### 9.3.2 `sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_project_p.sql`

Same as above, but adds `namespace` to SELECT and GROUP BY:

```sql
-- GROUP BY usage_start, cluster_id, cluster_alias, namespace, cost_model_rate_type, cost_model_rate_name
```

#### 9.3.3 `sql/openshift/ui_summary/reporting_ocp_cost_breakdown_by_node_p.sql`

Same, adds `node` to SELECT and GROUP BY.

#### 9.3.4 `sql/openshift/ui_summary/reporting_ocp_vm_breakdown_p.sql`

Mirrors `reporting_ocp_vm_summary_p.sql` but adds `cost_model_rate_name` GROUP BY. Includes VM-specific fields (`vm_name` extracted from `all_labels ->> 'vm_kubevirt_io_name'`).

### 9.4 Population Integration

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

### 9.5 Row Count Estimation

For a typical cost model with 5 rates and daily data over 1 month:

| Table | Existing rows/day | New rows/day | Multiplier |
|-------|-------------------|--------------|------------|
| `reporting_ocp_cost_summary_p` | ~5-10 (by rate_type) | — | unchanged |
| `reporting_ocp_cost_breakdown_p` | — | ~25-50 (by rate_type × rate_name) | new table |
| `reporting_ocp_cost_summary_by_project_p` | ~50-500 (by project × rate_type) | — | unchanged |
| `reporting_ocp_cost_breakdown_by_project_p` | — | ~250-2500 (by project × rate_type × rate_name) | new table |

These are acceptable sizes given monthly partitioning and source-scoped queries.

### 9.6 Files Changed

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

## 10. PR 6: API Layer — Provider Map, Query Handler, Serializers

### 10.1 ProviderMap Changes

**File:** `koku/api/report/ocp/provider_map.py`

#### 10.1.1 New Breakdown Annotations

Add a new report type or extend the existing `costs` / `costs_by_project` report types with a method to query breakdown data. The cleanest approach: add a helper that queries the breakdown table and returns a dict keyed by `(date, namespace/cluster/node, cost_model_rate_type)`.

Since the existing `ProviderMap` pattern works with a single `query_table`, and we need to query **two** tables (existing summary for aggregate + new breakdown for detail), the breakdown query is best handled as a **secondary query** in the query handler, not in the provider map annotations.

However, we still need to define the breakdown table in the provider map so the query handler knows which table to use:

```python
# In the 'costs' report type mapping:
"costs": {
    "default": {
        "default": {
            "tables": {
                "query": OCPCostSummaryP,
                "breakdown": OCPCostBreakdownP,  # NEW
            },
            # ... existing annotations, filters, group_by ...
        }
    }
},
"costs_by_project": {
    "default": {
        "default": {
            "tables": {
                "query": OCPCostSummaryByProjectP,
                "breakdown": OCPCostBreakdownByProjectP,  # NEW
            },
            # ...
        }
    }
},
```

#### 10.1.2 Breakdown PACK_DEFINITIONS

The breakdown data needs its own pack definition to structure the response:

```python
# No special PACK_DEFINITIONS needed for breakdown — it's a flat list
# of {name, source, value, units} objects, not the nested structure
# used by PACK_DEFINITIONS.
```

The breakdown is structured differently from the aggregate cost data, so it bypasses the `_pack_data_object()` mechanism entirely.

### 10.2 Query Handler Changes

**File:** `koku/api/report/ocp/query_handler.py`

#### 10.2.1 Breakdown Query

Add a method to query the breakdown table and attach results to the response:

```python
def _get_breakdown_data(self, date_filter, group_filter=None):
    """Query the breakdown summary table for per-rate-name data."""
    breakdown_table = self._mapper.report_type_map.get("tables", {}).get("breakdown")
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
```

#### 10.2.2 Overhead Proportional Breakdown

The overhead categories (platform_distributed, worker_distributed, etc.) don't have a `cost_model_rate_name`. Their breakdown is computed at query time by:

1. Getting the entity's (project/cluster/node) total cost composition by rate name from the breakdown table
2. Computing each rate name's share as a proportion of the entity's total non-overhead cost
3. Distributing each overhead category's total proportionally

```python
def _compute_overhead_breakdown(self, entity_breakdown, overhead_totals):
    """Compute proportional breakdown of overhead costs.

    Args:
        entity_breakdown: QuerySet of {cost_model_rate_name, total_cost} for the entity
        overhead_totals: Dict of {overhead_type: total_value}

    Returns:
        Dict of {overhead_type: [{name, source, value, units}]}
    """
    # Get non-overhead cost composition
    rate_costs = {}
    total_non_overhead = Decimal(0)
    for entry in entity_breakdown:
        rate_type = entry["cost_model_rate_type"]
        if rate_type in ("Infrastructure", "Supplementary") and entry["cost_model_rate_name"]:
            name = entry["cost_model_rate_name"]
            cost = entry["total_cost"] or Decimal(0)
            rate_costs[name] = rate_costs.get(name, Decimal(0)) + cost
            total_non_overhead += cost

    if total_non_overhead == 0:
        return {}

    # Compute proportional breakdown for each overhead type
    overhead_breakdown = {}
    for overhead_type, overhead_total in overhead_totals.items():
        if not overhead_total:
            continue
        breakdown = []
        for name, cost in sorted(rate_costs.items(), key=lambda x: x[1], reverse=True):
            proportion = cost / total_non_overhead
            breakdown_value = (overhead_total * proportion).quantize(Decimal("0.01"))
            if breakdown_value > 0:
                breakdown.append({
                    "name": name,
                    "source": "rate",
                    "value": breakdown_value,
                    "units": "USD",  # From currency annotation
                })
        overhead_breakdown[overhead_type] = breakdown

    return overhead_breakdown
```

#### 10.2.3 Response Assembly

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

        # Usage breakdown: rate-attributed costs
        usage_breakdown = self._build_usage_breakdown(breakdown_qs)
        if usage_breakdown and "usage" in cost:
            cost["usage"]["breakdown"] = usage_breakdown

        # Overhead proportional breakdown
        overhead_types = {
            "platform_distributed": cost.get("platform_distributed", {}).get("value"),
            "worker_unallocated_distributed": cost.get("worker_unallocated_distributed", {}).get("value"),
            "storage_unattributed_distributed": cost.get("storage_unattributed_distributed", {}).get("value"),
            "network_unattributed_distributed": cost.get("network_unattributed_distributed", {}).get("value"),
            "gpu_unallocated_distributed": cost.get("gpu_unallocated_distributed", {}).get("value"),
        }
        overhead_breakdown = self._compute_overhead_breakdown(breakdown_qs, overhead_types)
        for oh_type, oh_breakdown in overhead_breakdown.items():
            if oh_type in cost and oh_breakdown:
                cost[oh_type]["breakdown"] = oh_breakdown

    # Also attach breakdown to each data row (per date, per group-by value)
    self._attach_breakdown_to_data_rows(output.get("data", []))

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
```

#### 10.2.4 Per-Data-Row Breakdown

The breakdown should also appear on each row in the `data` array (each date's data, each group-by entity's data). This requires querying the breakdown table per `(usage_start, namespace/cluster/node)`:

```python
def _attach_breakdown_to_data_rows(self, data):
    """Attach breakdown arrays to each data row."""
    # Pre-fetch all breakdown data for the date range
    all_breakdown = self._get_breakdown_data(self.query_filter)

    # Index by (date, group_by_key)
    breakdown_index = defaultdict(list)
    for entry in all_breakdown:
        # ... index by date and group_by ...
        pass

    # Attach to each data row
    for date_entry in data:
        for row in date_entry.get(self._group_by_key, [date_entry]):
            # Look up breakdown for this row
            # ... attach to cost.usage.breakdown, cost.platform_distributed.breakdown, etc.
            pass
```

**Performance note:** This secondary query runs against the breakdown summary table (which is already partitioned and indexed). For a typical request spanning 30 days with ~50 projects and ~5 rates, this is ~7,500 rows — well within acceptable query time.

### 10.3 Serializer Changes

No serializer changes needed for the report API — the `breakdown` array is an additive extension to the response, not a new query parameter. Existing serializers for `group_by`, `filter`, and `order_by` remain unchanged.

### 10.4 Files Changed

| File | Change |
|------|--------|
| `koku/api/report/ocp/provider_map.py` | Add breakdown table references |
| `koku/api/report/ocp/query_handler.py` | Breakdown query, overhead computation, response assembly |
| `koku/api/report/all/openshift/provider_map.py` | Add breakdown table references (OCP-All view) |
| `koku/api/report/all/openshift/query_handler.py` | Extend with breakdown logic |

---

## 11. PR 7: Trino and Self-Hosted SQL Paths

### 11.1 Trino SQL Changes

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
| `distribute_cost/distribute_unallocated_gpu_cost.sql` | **No changes** (distribution SQL) |

Trino distribution SQL files (`distribute_cost/`) — **no changes needed** (same rationale as PostgreSQL).

#### 11.1.1 Trino Parquet Schema

The `cost_model_rate_name` column must also be added to the Parquet schema for Trino to recognize it. This is handled by the Hive metastore schema — when the Django migration adds the column to PostgreSQL, the Trino table DDL must also be updated.

Check if Trino reads from the PostgreSQL summary tables or from S3 parquet files. Based on the codebase analysis, Trino queries run against Hive tables backed by S3 parquet data for line items, but summary tables are in PostgreSQL. The `usage_costs.sql` in the `trino_sql/` path writes results back to PostgreSQL (via `_execute_trino_multipart_sql_query` which writes to the line item table). So the column addition to PostgreSQL is sufficient for the Trino write path.

However, if Trino reads from `reporting_ocpusagelineitem_daily_summary` (which it doesn't — Trino reads from S3 parquet), this would need parquet schema changes. Since Trino cost model SQL writes TO PostgreSQL and reads FROM Hive/S3 parquet source data, no parquet schema changes are needed.

### 11.2 Self-Hosted SQL Changes

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
| `distribute_cost/distribute_unallocated_gpu_cost.sql` | **No changes** (distribution SQL) |

### 11.3 Verification

After changes, verify both paths produce the same `cost_model_rate_name` values by:
1. Running cost model application for a test provider on both cloud and self-hosted paths
2. Comparing the `cost_model_rate_name` column values in `reporting_ocpusagelineitem_daily_summary`

### 11.4 Files Changed

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

## 12. Cross-Cutting Concerns

### 12.1 Distribution SQL — No Changes

The five distribution SQL files (`distribute_platform_cost.sql`, `distribute_worker_cost.sql`, `distribute_unattributed_storage_cost.sql`, `distribute_unattributed_network_cost.sql`, `distribute_unallocated_gpu_cost.sql`) are **not modified**. Rationale:

1. Distribution computes aggregate pools (total platform cost, total worker cost) and distributes proportionally by CPU/memory usage
2. The source cost composition (which rates contributed) is not tracked in distribution
3. Adding per-rate tracking to distribution would require a fundamentally different algorithm (distribute each rate's contribution separately) with no benefit — the proportional breakdown at query time achieves the same result
4. Distribution rows intentionally have `cost_model_rate_name = NULL`

### 12.2 Delete/Cleanup Logic

The `delete_monthly_cost.sql` and `delete_monthly_cost_model_rate_type.sql` files do **not** need changes for `cost_model_rate_name`. They delete by `cost_model_rate_type` and `monthly_cost_type`, which is sufficient for cleanup before re-insertion.

### 12.3 `_update_markup_cost()` — No Changes

Markup cost is computed via Django ORM annotations (`infrastructure_raw_cost * markup_percentage`), not via SQL template. It operates on existing rows. No `cost_model_rate_name` is set because markup is not a "rate" — it's a percentage applied to raw cost.

In Phase 1, markup breakdown in the API response is deferred (markup applies to cloud raw cost, which doesn't have per-service granularity until Phase 2). The API returns markup as a single aggregate, which is the current behavior.

### 12.4 Currency Handling

The `cost_model_rate_name` is a text field and does not affect currency handling. The `raw_currency` field on line items continues to work as before. Breakdown entries in the API response inherit the currency from the parent cost category.

### 12.5 Forecasting

Forecasting uses the existing summary tables. Since we're not modifying those tables, forecasting is unaffected. If forecasting needs per-rate granularity in the future, it can query the new breakdown tables.

### 12.6 CSV Export

CSV export currently uses the summary table data. The breakdown data is not included in CSV exports in Phase 1. This is noted as a follow-up item in the PRD.

### 12.7 RBAC / Permissions

The breakdown data follows the same permission model as the existing cost data. If a user can see a project's costs, they can see the per-rate breakdown. No additional RBAC rules needed.

---

## 13. Phase 2 Notes (COST-4415)

Phase 2 adds cloud service breakdown for raw cost. Here's a brief sketch of the required changes:

### 13.1 Raw Cost Breakdown

OCP-on-cloud summary tables already have `product_code` (AWS/GCP) or `service_name` (Azure). The breakdown table for Phase 2 needs to GROUP BY this field in addition to `cost_model_rate_name`.

**New column on breakdown tables:** `cloud_service_name` (TextField, nullable). For Phase 1 rows this is NULL; for Phase 2 it contains the service name.

Alternatively, extend the existing breakdown tables to include `product_code`/`service_name` as a GROUP BY dimension, since the OCP-on-cloud line items already carry this information.

### 13.2 Markup Breakdown

Markup = `infrastructure_raw_cost * markup_percentage`. Since raw cost will be per-service in Phase 2:

```sql
-- Per-service markup:
markup_per_service = raw_cost_per_service * markup_percentage
```

This can be computed at query time by multiplying each service's raw cost by the markup rate, or pre-computed in the breakdown summary table.

### 13.3 Overhead Service-Level Breakdown

With Phase 2, overhead breakdown includes both rate names (from Phase 1) and cloud services. The proportional computation at query time naturally extends:

```
entity_total = sum(rate_costs) + sum(service_costs)
rate_share = rate_cost / entity_total
service_share = service_cost / entity_total
overhead_breakdown_entry = overhead_total * share
```

### 13.4 API Response

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

## 14. Testing Strategy

### 14.1 Unit Tests

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

### 14.2 Integration Tests

| Scenario | Verification |
|----------|-------------|
| Cost model with 3 tiered rates (CPU, memory, volume) | 3 separate rows with distinct `cost_model_rate_name` in line item table |
| Cost model with tag-based rates | Tag cost rows carry `cost_model_rate_name` from parent rate |
| Cost model with monthly rates (node, cluster, PVC, VM) | Monthly cost rows carry `cost_model_rate_name` |
| Distribution after cost application | Distributed rows have `cost_model_rate_name = NULL` |
| Breakdown summary tables populated correctly | Row counts match expectation (rate_type × rate_name × entity) |
| API response includes `breakdown` on usage | Array of {name, source, value, units} entries |
| API response includes `breakdown` on overhead | Proportional computation matches manual calculation |
| On-prem path (self_hosted_sql) | Same behavior as cloud PostgreSQL path |
| Trino path (VM rates) | Rate name written correctly via Trino SQL |
| Existing API response unchanged | All existing fields/values identical |

### 14.3 Performance Tests

| Scenario | Metric | Threshold |
|----------|--------|-----------|
| Cost model application with 10 rates | Time to complete | < 2x current |
| Breakdown summary population | Time to complete | < 1.5x current UI summary |
| API query with breakdown | Response time | < 1.5x current |
| Line item table row count after cost application | Row increase | ~5-10x for cost-model rows |

---

## 15. Migration and Rollback Plan

### 15.1 Forward Migration

1. **PR 1** merges: Data migration populates `name` on existing rates. API accepts `name` on create/update.
2. **PR 2** merges: `cost_model_rate_name` column added (NULL, no data yet). No behavioral change.
3. **PRs 3-4** merge: Next cost model application writes `cost_model_rate_name`. Historical data remains NULL until re-processed.
4. **PR 5** merges: Breakdown summary tables created. Populated on next cost model application cycle.
5. **PR 6** merges: API returns `breakdown` array. For projects not yet re-processed, `breakdown` is empty.
6. **PR 7** merges: Trino and self-hosted paths also write `cost_model_rate_name`.

### 15.2 Backfill

After all PRs merge, a one-time re-processing of all providers triggers cost model re-application, which:
- Writes `cost_model_rate_name` on all line item rows
- Populates breakdown summary tables for all historical data

This can be triggered via the existing `update_summary_cost_model_costs()` task.

### 15.3 Rollback

Each PR can be reverted independently:
- **PR 6 revert**: API stops returning `breakdown`. No data loss.
- **PR 5 revert**: Breakdown tables stop being populated. Can be dropped.
- **PRs 3-4 revert**: Cost application stops writing `cost_model_rate_name`. Column remains but unused.
- **PR 2 revert**: Column migration revert (drop column). Data loss is acceptable since it's derived.
- **PR 1 revert**: Data migration reverse populates NULL names. API stops accepting `name`. Need to handle existing rates that now have names.

---

## 16. Open Questions and Decisions

| # | Question | Status | Decision |
|---|----------|--------|----------|
| 1 | Top-N limiting for breakdown entries | Open | Recommend top 10 by value, rest as "Other". Implement in query handler. |
| 2 | Multiple tiered rates for same metric | Decided | Separate rows per rate name (natural from per-rate execution). Aggregate in API if needed. |
| 3 | Infrastructure vs Supplementary in breakdown | Decided | Merge under rate name. Rate name is the user-facing concept. |
| 4 | CSV export of breakdown data | Deferred | Follow-up after Phase 1 GA. |
| 5 | Breakdown for tag group-by view | Open | Tag group-by queries a different code path in query handler. Need to verify breakdown table has sufficient data. |
| 6 | GPU rate name attribution | Open | GPU cost model is feature-flagged. When enabled, `gpu_cost_per_month` rate should carry `cost_model_rate_name` like other rates. |
| 7 | Breakdown summary table cleanup | Open | Should cleanup of breakdown tables be coupled with cleanup of existing summary tables? Recommend: yes, same lifecycle in `populate_ui_summary_tables`. |
