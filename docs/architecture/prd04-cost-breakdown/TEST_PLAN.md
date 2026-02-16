# TDD Test Plan: Cost Breakdown for Custom Costs (Phase 1)

**Reference:** [Detailed Design](DETAILED_DESIGN.md) | [PRD](README.md)
**Methodology:** Red → Green → Refactor per PR
**Last updated:** 2026-02-16

---

## How to Read This Document

Each PR section follows the TDD cycle:

1. **RED** — Write the test first. It must fail because the feature doesn't exist yet.
2. **GREEN** — Write the minimum code to make the test pass.
3. **REFACTOR** — Clean up the code while keeping all tests green.

Tests are numbered `T<PR>.<seq>` for cross-referencing. Dependencies on prior PRs are noted where tests require data from earlier work.

---

## Table of Contents

1. [PR 1: Cost Model Rate Name Field](#pr-1-cost-model-rate-name-field)
2. [PR 2: Line Item Table Column](#pr-2-line-item-table-column)
3. [PR 3: Rate Name Threading](#pr-3-rate-name-threading)
4. [PR 4: Tiered Usage Rate Refactoring](#pr-4-tiered-usage-rate-refactoring)
5. [PR 5: Distribution SQL Per-Rate-Name Tracking](#pr-5-distribution-sql-per-rate-name-tracking)
6. [PR 6: Breakdown Summary Tables](#pr-6-breakdown-summary-tables)
7. [PR 7: API Layer](#pr-7-api-layer)
8. [PR 8: Trino and Self-Hosted SQL Paths](#pr-8-trino-and-self-hosted-sql-paths)
9. [End-to-End Integration Tests](#end-to-end-integration-tests)

---

## PR 1: Cost Model Rate Name Field

**Files under test:**
- `koku/cost_models/serializers.py`
- `koku/cost_models/migrations/NNNN_rate_name_migration.py`

**Test file:** `koku/cost_models/test/test_serializers.py`

### RED phase — write these tests first

#### T1.1 `test_rate_serializer_name_required`

```python
class RateSerializerNameTest(TestCase):
    """Tests for the name field on RateSerializer."""

    def test_rate_serializer_name_required(self):
        """Rate without name is rejected."""
        rate_data = {
            "metric": {"name": "cpu_core_usage_per_hour"},
            "tiered_rates": [{"value": 0.05, "unit": "USD"}],
            "cost_type": "Infrastructure",
            # no "name" field
        }
        serializer = RateSerializer(data=rate_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)
```

**Fails because:** `RateSerializer` has no `name` field yet.

#### T1.2 `test_rate_serializer_name_max_length`

```python
    def test_rate_serializer_name_max_length(self):
        """Rate name exceeding 50 characters is rejected."""
        rate_data = {
            "name": "X" * 51,
            "metric": {"name": "cpu_core_usage_per_hour"},
            "tiered_rates": [{"value": 0.05, "unit": "USD"}],
            "cost_type": "Infrastructure",
        }
        serializer = RateSerializer(data=rate_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)
```

**Fails because:** No `name` field with `max_length=50`.

#### T1.3 `test_rate_serializer_name_accepted`

```python
    def test_rate_serializer_name_accepted(self):
        """Rate with valid name is accepted."""
        rate_data = {
            "name": "CPU charge",
            "metric": {"name": "cpu_core_usage_per_hour"},
            "tiered_rates": [{"value": 0.05, "unit": "USD"}],
            "cost_type": "Infrastructure",
        }
        serializer = RateSerializer(data=rate_data)
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["name"], "CPU charge")
```

**Fails because:** No `name` field on `RateSerializer`.

#### T1.4 `test_cost_model_duplicate_rate_names_rejected`

```python
class CostModelSerializerNameTest(IamTestCase):
    """Tests for rate name uniqueness within a cost model."""

    def test_cost_model_duplicate_rate_names_rejected(self):
        """Two rates with the same name in one cost model are rejected."""
        data = {
            "name": "Test Cost Model",
            "source_type": Provider.PROVIDER_OCP,
            "providers": [{"uuid": self.provider.uuid, "name": self.provider.name}],
            "rates": [
                {
                    "name": "CPU charge",
                    "metric": {"name": "cpu_core_usage_per_hour"},
                    "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
                {
                    "name": "CPU charge",  # duplicate
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
```

**Fails because:** No uniqueness validation on rate names.

#### T1.5 `test_cost_model_unique_rate_names_accepted`

```python
    def test_cost_model_unique_rate_names_accepted(self):
        """Two rates with different names in one cost model are accepted."""
        data = {
            "name": "Test Cost Model",
            "source_type": Provider.PROVIDER_OCP,
            "providers": [{"uuid": self.provider.uuid, "name": self.provider.name}],
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
```

**Fails because:** No `name` field on `RateSerializer`.

#### T1.6 `test_cost_model_api_response_includes_name`

```python
    def test_cost_model_api_response_includes_name(self):
        """GET cost model response includes name in each rate."""
        # Create a cost model with named rates (via serializer or ORM)
        # ... setup ...
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
```

**Fails because:** `name` not in serializer output.

### Migration tests

**Test file:** `koku/cost_models/test/test_migrations.py` (new)

#### T1.7 `test_migration_generates_name_from_description`

```python
class RateNameMigrationTest(MasuTestCase):
    """Test the rate name data migration logic."""

    def test_migration_generates_name_from_description(self):
        """Rate with description gets name derived from description."""
        rate = {"description": "JBoss middleware license", "metric": {"name": "cpu_core_usage_per_hour"}, "cost_type": "Infrastructure"}
        used_names = set()
        name = _generate_name(rate, used_names)
        self.assertEqual(name, "JBoss middleware license")
```

**Note:** Test the `_generate_name` function directly (extracted as a module-level function for testability).

#### T1.8 `test_migration_truncates_long_description`

```python
    def test_migration_truncates_long_description(self):
        """Description > 50 chars is truncated to 47 + '...'."""
        rate = {"description": "A" * 60, "metric": {"name": "cpu_core_usage_per_hour"}, "cost_type": "Infrastructure"}
        used_names = set()
        name = _generate_name(rate, used_names)
        self.assertEqual(len(name), 50)
        self.assertTrue(name.endswith("..."))
```

#### T1.9 `test_migration_generates_name_from_metric_when_no_description`

```python
    def test_migration_generates_name_from_metric_when_no_description(self):
        """Rate without description gets name from metric + cost_type."""
        rate = {"metric": {"name": "cpu_core_usage_per_hour"}, "cost_type": "Infrastructure"}
        used_names = set()
        name = _generate_name(rate, used_names)
        self.assertEqual(name, "cpu_core_usage_per_hour_infrastructure")
```

#### T1.10 `test_migration_deduplicates_with_numeric_suffix`

```python
    def test_migration_deduplicates_with_numeric_suffix(self):
        """Duplicate candidate names get numeric suffixes."""
        rate = {"description": "CPU rate", "metric": {"name": "cpu_core_usage_per_hour"}, "cost_type": "Infrastructure"}
        used_names = {"CPU rate"}
        name = _generate_name(rate, used_names)
        self.assertEqual(name, "CPU rate_000")
        self.assertNotIn(name, used_names - {name})
```

#### T1.11 `test_migration_preserves_existing_names`

```python
    def test_migration_preserves_existing_names(self):
        """Rates that already have names are not re-generated."""
        rates = [
            {"name": "My CPU rate", "metric": {"name": "cpu_core_usage_per_hour"}, "cost_type": "Infrastructure"},
            {"metric": {"name": "memory_gb_usage_per_hour"}, "cost_type": "Infrastructure"},
        ]
        # Run populate_rate_names logic
        # Assert first rate retains "My CPU rate", second rate gets auto-generated name
```

### GREEN phase

Implement in this order:

1. Add `name = serializers.CharField(max_length=50, required=True)` to `RateSerializer` → T1.1, T1.2, T1.3, T1.5 pass
2. Add `validate_rates()` uniqueness check in `CostModelSerializer` → T1.4 passes
3. Add `name` to serializer output → T1.6 passes
4. Write `_generate_name()` and `populate_rate_names()` migration → T1.7–T1.11 pass

### REFACTOR phase

- Extract `_generate_name()` to a module-level utility for reuse and independent testability
- Ensure `validate_rates()` error messages include the duplicate name for debugging

---

## PR 2: Line Item Table Column

**Files under test:**
- `koku/reporting/provider/ocp/models.py`
- `koku/reporting/migrations/0344_add_cost_model_rate_name.py`

**Test file:** `koku/reporting/test/test_models.py` (or inline in existing model tests)

### RED phase

#### T2.1 `test_line_item_has_cost_model_rate_name_field`

```python
class OCPLineItemRateNameTest(MasuTestCase):
    """Test cost_model_rate_name on OCPUsageLineItemDailySummary."""

    def test_line_item_has_cost_model_rate_name_field(self):
        """OCPUsageLineItemDailySummary has cost_model_rate_name field."""
        field = OCPUsageLineItemDailySummary._meta.get_field("cost_model_rate_name")
        self.assertIsNotNone(field)
        self.assertTrue(field.null)
```

**Fails because:** Field doesn't exist on the model.

#### T2.2 `test_line_item_rate_name_accepts_text`

```python
    def test_line_item_rate_name_accepts_text(self):
        """cost_model_rate_name can store text values."""
        with schema_context(self.schema):
            item = baker.make(
                OCPUsageLineItemDailySummary,
                cost_model_rate_name="CPU charge",
                usage_start=self.start_date,
            )
            item.refresh_from_db()
            self.assertEqual(item.cost_model_rate_name, "CPU charge")
```

**Fails because:** Column doesn't exist in DB.

#### T2.3 `test_line_item_rate_name_nullable`

```python
    def test_line_item_rate_name_nullable(self):
        """cost_model_rate_name defaults to NULL."""
        with schema_context(self.schema):
            item = baker.make(
                OCPUsageLineItemDailySummary,
                usage_start=self.start_date,
            )
            item.refresh_from_db()
            self.assertIsNone(item.cost_model_rate_name)
```

**Fails because:** Column doesn't exist.

### GREEN phase

1. Add `cost_model_rate_name = models.TextField(null=True)` to model → T2.1, T2.3 pass
2. Add index, write migration → T2.2 passes after migration runs

### REFACTOR phase

- Verify index is used by running `EXPLAIN` on a GROUP BY query (manual check, not automated)

---

## PR 3: Rate Name Threading

**Files under test:**
- `koku/masu/database/cost_model_db_accessor.py`
- `koku/masu/processor/ocp/ocp_cost_model_cost_updater.py`
- `koku/masu/database/ocp_report_db_accessor.py`
- SQL templates: tag rates, monthly rates

**Test files:**
- `koku/masu/test/database/test_cost_model_db_accessor.py`
- `koku/masu/test/processor/ocp/test_ocp_cost_model_cost_updater.py`
- `koku/masu/test/database/test_ocp_report_db_accessor.py`

### RED phase — CostModelDBAccessor

#### T3.1 `test_infrastructure_rates_by_name_returns_list`

```python
class CostModelDBAccessorRatesByNameTest(MasuTestCase):
    """Tests for rate-name-aware accessor properties."""

    def setUp(self):
        super().setUp()
        self.rates = [
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
        ]
        self.cost_model = self.creator.create_cost_model(
            self.provider_uuid, Provider.PROVIDER_OCP, self.rates
        )

    def test_infrastructure_rates_by_name_returns_list(self):
        """infrastructure_rates_by_name returns a list of {metric, value, name} dicts."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as accessor:
            rates = accessor.infrastructure_rates_by_name
        self.assertIsInstance(rates, list)
        self.assertEqual(len(rates), 2)
        metrics = {r["metric"] for r in rates}
        self.assertEqual(metrics, {"cpu_core_usage_per_hour", "memory_gb_usage_per_hour"})
        for r in rates:
            self.assertIn("name", r)
            self.assertIn("value", r)
            self.assertIn("metric", r)
```

**Fails because:** `infrastructure_rates_by_name` property doesn't exist.

#### T3.2 `test_infrastructure_rates_by_name_preserves_duplicates`

```python
    def test_infrastructure_rates_by_name_preserves_duplicates(self):
        """Two rates for same metric both appear in the list."""
        rates = [
            {
                "name": "Base CPU",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
            {
                "name": "Premium CPU",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.03, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
        ]
        cost_model = self.creator.create_cost_model(
            self.provider_uuid, Provider.PROVIDER_OCP, rates
        )
        with CostModelDBAccessor(self.schema, self.provider_uuid) as accessor:
            by_name = accessor.infrastructure_rates_by_name
        cpu_rates = [r for r in by_name if r["metric"] == "cpu_core_usage_per_hour"]
        self.assertEqual(len(cpu_rates), 2)
        names = {r["name"] for r in cpu_rates}
        self.assertEqual(names, {"Base CPU", "Premium CPU"})
```

**Fails because:** Property doesn't exist.

#### T3.3 `test_supplementary_rates_by_name`

```python
    def test_supplementary_rates_by_name(self):
        """supplementary_rates_by_name returns supplementary rates only."""
        rates = [
            {"name": "CPU infra", "metric": {"name": "cpu_core_usage_per_hour"},
             "tiered_rates": [{"value": 0.05, "unit": "USD"}], "cost_type": "Infrastructure"},
            {"name": "CPU supp", "metric": {"name": "cpu_core_usage_per_hour"},
             "tiered_rates": [{"value": 0.02, "unit": "USD"}], "cost_type": "Supplementary"},
        ]
        cost_model = self.creator.create_cost_model(
            self.provider_uuid, Provider.PROVIDER_OCP, rates
        )
        with CostModelDBAccessor(self.schema, self.provider_uuid) as accessor:
            supp = accessor.supplementary_rates_by_name
        self.assertEqual(len(supp), 1)
        self.assertEqual(supp[0]["name"], "CPU supp")
        self.assertEqual(supp[0]["cost_type"] if "cost_type" in supp[0] else "Supplementary", "Supplementary")
```

**Fails because:** Property doesn't exist.

#### T3.4 `test_tag_rate_names_mapping`

```python
    def test_tag_rate_names_mapping(self):
        """tag_rate_names returns {metric: {tag_key: rate_name}} mapping."""
        rates = [
            {
                "name": "JBoss subscription",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tag_rates": {
                    "tag_key": "workload",
                    "tag_values": [{"tag_value": "jboss", "value": 40.0}],
                },
                "cost_type": "Infrastructure",
            },
        ]
        cost_model = self.creator.create_cost_model(
            self.provider_uuid, Provider.PROVIDER_OCP, rates
        )
        with CostModelDBAccessor(self.schema, self.provider_uuid) as accessor:
            names = accessor.tag_rate_names
        self.assertEqual(names["cpu_core_usage_per_hour"]["workload"], "JBoss subscription")
```

**Fails because:** `tag_rate_names` property doesn't exist.

#### T3.5 `test_metric_to_tag_params_map_includes_name`

```python
    def test_metric_to_tag_params_map_includes_name(self):
        """metric_to_tag_params_map entries include 'name' key."""
        rates = [
            {
                "name": "GPU tag rate",
                "metric": {"name": "gpu_request_per_gpu_hour"},
                "tag_rates": {
                    "tag_key": "gpu_type",
                    "tag_values": [{"tag_value": "a100", "value": 5.0, "default": True}],
                },
                "cost_type": "Infrastructure",
            },
        ]
        cost_model = self.creator.create_cost_model(
            self.provider_uuid, Provider.PROVIDER_OCP, rates
        )
        with CostModelDBAccessor(self.schema, self.provider_uuid) as accessor:
            tag_map = accessor.metric_to_tag_params_map
        for metric, params_list in tag_map.items():
            for params in params_list:
                self.assertIn("name", params)
```

**Fails because:** `name` key not in tag params.

#### T3.6 `test_existing_infrastructure_rates_unchanged`

```python
    def test_existing_infrastructure_rates_unchanged(self):
        """Existing infrastructure_rates property is not broken."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as accessor:
            old_rates = accessor.infrastructure_rates
        # Should still be a dict keyed by metric with scalar values
        self.assertIsInstance(old_rates, dict)
        self.assertIn("cpu_core_usage_per_hour", old_rates)
        self.assertIsInstance(old_rates["cpu_core_usage_per_hour"], (int, float, Decimal))
```

**Passes already** — this is a regression guard. Include it to ensure no breaking change.

### RED phase — OCPCostModelCostUpdater

#### T3.7 `test_monthly_cost_passes_rate_name`

```python
class OCPCostModelCostUpdaterRateNameTest(MasuTestCase):
    """Tests for rate_name threading through the cost updater."""

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_monthly_cost_sql")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_monthly_cost_passes_rate_name(self, mock_accessor_cls, mock_populate):
        """_update_monthly_cost passes rate_name to populate_monthly_cost_sql."""
        mock_accessor = mock_accessor_cls.return_value.__enter__.return_value
        mock_accessor.infrastructure_rates = {"node_cost_per_month": Decimal("100")}
        mock_accessor.supplementary_rates = {}
        mock_accessor.infrastructure_rates_by_name = [
            {"metric": "node_cost_per_month", "value": Decimal("100"), "name": "Node charge"},
        ]
        mock_accessor.supplementary_rates_by_name = []
        # ... other required mock attributes ...

        updater = OCPCostModelCostUpdater(self.schema, self.provider)
        updater._update_monthly_cost(self.start_date, self.end_date)

        # Assert populate_monthly_cost_sql was called with rate_name="Node charge"
        call_kwargs = mock_populate.call_args
        self.assertEqual(call_kwargs.kwargs.get("rate_name") or call_kwargs[1].get("rate_name"), "Node charge")
```

**Fails because:** `_update_monthly_cost` doesn't accept or pass `rate_name`.

#### T3.8 `test_multiple_monthly_rates_same_metric_both_applied`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_monthly_cost_sql")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_multiple_monthly_rates_same_metric_both_applied(self, mock_accessor_cls, mock_populate):
        """Two node_cost_per_month rates with different names both get SQL calls."""
        mock_accessor = mock_accessor_cls.return_value.__enter__.return_value
        mock_accessor.infrastructure_rates = {"node_cost_per_month": Decimal("100")}
        mock_accessor.supplementary_rates = {}
        mock_accessor.infrastructure_rates_by_name = [
            {"metric": "node_cost_per_month", "value": Decimal("50"), "name": "Base node"},
            {"metric": "node_cost_per_month", "value": Decimal("30"), "name": "Premium node"},
        ]
        mock_accessor.supplementary_rates_by_name = []
        # ... other required mock attributes ...

        updater = OCPCostModelCostUpdater(self.schema, self.provider)
        updater._update_monthly_cost(self.start_date, self.end_date)

        # Assert populate_monthly_cost_sql was called twice for node_cost_per_month
        node_calls = [
            c for c in mock_populate.call_args_list
            if "node" in str(c).lower() or "Node" in str(c)
        ]
        self.assertEqual(len(node_calls), 2)
        rate_names = {c.kwargs.get("rate_name") for c in node_calls}
        self.assertEqual(rate_names, {"Base node", "Premium node"})
```

**Fails because:** `_update_monthly_cost` uses dict lookup (one rate per metric).

#### T3.9 `test_tag_usage_costs_passes_tag_rate_names`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_tag_usage_costs")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_tag_usage_costs_passes_tag_rate_names(self, mock_accessor_cls, mock_populate):
        """_update_tag_usage_costs passes tag_rate_names to populate_tag_usage_costs."""
        mock_accessor = mock_accessor_cls.return_value.__enter__.return_value
        mock_accessor.tag_infrastructure_rates = {"cpu_core_usage_per_hour": {"workload": {"jboss": Decimal("40")}}}
        mock_accessor.tag_supplementary_rates = {}
        mock_accessor.tag_rate_names = {"cpu_core_usage_per_hour": {"workload": "JBoss subscription"}}
        # ... other required mock attributes ...

        updater = OCPCostModelCostUpdater(self.schema, self.provider)
        updater._update_tag_usage_costs(self.start_date, self.end_date)

        call_kwargs = mock_populate.call_args
        self.assertIn("tag_rate_names", call_kwargs.kwargs)
```

**Fails because:** `_update_tag_usage_costs` doesn't pass `tag_rate_names`.

#### T3.9b `test_monthly_tag_based_cost_passes_rate_name`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_tag_cost_sql")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_monthly_tag_based_cost_passes_rate_name(self, mock_accessor_cls, mock_populate):
        """_update_monthly_tag_based_cost passes name from metric_to_tag_params_map."""
        mock_accessor = mock_accessor_cls.return_value.__enter__.return_value
        mock_accessor.metric_to_tag_params_map = {
            "node_cost_per_month": [{
                "rate_type": "Infrastructure",
                "tag_key": "workload",
                "default_rate": "100.00",
                "value_rates": {"jboss": "40.00"},
                "name": "JBoss tag rate",
            }],
        }
        # ... other required mock attributes ...

        updater = OCPCostModelCostUpdater(self.schema, self.provider)
        updater._update_monthly_tag_based_cost(self.start_date, self.end_date)

        call_kwargs = mock_populate.call_args
        self.assertEqual(call_kwargs.kwargs.get("rate_name"), "JBoss tag rate")
```

**Fails because:** `_update_monthly_tag_based_cost` doesn't read or pass `name`.

#### T3.9c `test_populate_tag_based_costs_passes_rate_name`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    def test_populate_tag_based_costs_passes_rate_name(self, mock_execute):
        """populate_tag_based_costs reads name from metric_to_tag_params_map and passes to SQL."""
        metric_to_tag_params_map = {
            "gpu_request_per_gpu_hour": [{
                "rate_type": "Infrastructure",
                "tag_key": "gpu_type",
                "default_rate": "5.00",
                "value_rates": {"a100": "10.00"},
                "name": "GPU A100 rate",
            }],
        }
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_tag_based_costs(
                self.start_date, self.end_date, self.provider_uuid,
                metric_to_tag_params_map, cluster_params={},
            )
        # Find the call that used GPU SQL
        for call_args in mock_execute.call_args_list:
            sql_params = call_args[0][2] if len(call_args[0]) > 2 else {}
            if "rate_name" in sql_params:
                self.assertEqual(sql_params["rate_name"], "GPU A100 rate")
                break
        else:
            self.fail("No SQL call included rate_name parameter")
```

**Fails because:** `populate_tag_based_costs` doesn't read or pass `rate_name`.

### RED phase — OCPReportDBAccessor (SQL parameter threading)

#### T3.10 `test_populate_monthly_cost_sql_includes_rate_name_param`

```python
class OCPReportDBAccessorRateNameTest(MasuTestCase):
    """Tests for rate_name parameter threading to SQL."""

    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    def test_populate_monthly_cost_sql_includes_rate_name_param(self, mock_execute):
        """populate_monthly_cost_sql passes rate_name in SQL params."""
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_monthly_cost_sql(
                "Node", "node_cost_per_month", Decimal("100"),
                self.start_date, self.end_date, "cpu",
                self.provider_uuid, rate_name="Node charge",
            )
        sql_params = mock_execute.call_args[0][2]  # third positional arg
        self.assertEqual(sql_params["rate_name"], "Node charge")
```

**Fails because:** `populate_monthly_cost_sql` doesn't accept `rate_name`.

#### T3.11 `test_populate_monthly_cost_sql_rate_name_defaults_none`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    def test_populate_monthly_cost_sql_rate_name_defaults_none(self, mock_execute):
        """rate_name defaults to None (→ SQL NULL) when not provided (backward compat)."""
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_monthly_cost_sql(
                "Node", "node_cost_per_month", Decimal("100"),
                self.start_date, self.end_date, "cpu",
                self.provider_uuid,
            )
        sql_params = mock_execute.call_args[0][2]
        self.assertIsNone(sql_params["rate_name"])
```

**Fails because:** `rate_name` not in SQL params.

#### T3.12 `test_populate_tag_cost_sql_includes_rate_name`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._prepare_and_execute_raw_sql_query")
    def test_populate_tag_cost_sql_includes_rate_name(self, mock_execute):
        """populate_tag_cost_sql passes rate_name in SQL params."""
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_tag_cost_sql(
                "Node", "node_cost_per_month", "workload", {"jboss": Decimal("40")},
                self.start_date, self.end_date, "cpu",
                self.provider_uuid, rate_name="JBoss tag rate",
            )
        sql_params = mock_execute.call_args[0][2]
        self.assertEqual(sql_params["rate_name"], "JBoss tag rate")
```

**Fails because:** `populate_tag_cost_sql` doesn't accept `rate_name`.

### RED phase — Integration: SQL actually writes `cost_model_rate_name`

#### T3.13 `test_monthly_cost_sql_writes_rate_name_to_db`

```python
    def test_monthly_cost_sql_writes_rate_name_to_db(self):
        """After populate_monthly_cost_sql, line items have cost_model_rate_name set."""
        # Requires PR 1 + PR 2 to be merged (name field + column exist)
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_monthly_cost_sql(
                "Node", "node_cost_per_month", Decimal("100"),
                self.start_date, self.end_date, "cpu",
                self.provider_uuid, rate_name="Node charge",
            )
        with schema_context(self.schema):
            rows = OCPUsageLineItemDailySummary.objects.filter(
                monthly_cost_type="Node",
                cost_model_rate_type="Infrastructure",
                usage_start__gte=self.start_date,
            )
            for row in rows:
                self.assertEqual(row.cost_model_rate_name, "Node charge")
```

**Fails because:** SQL template doesn't include `cost_model_rate_name`.

#### T3.14 `test_tag_rate_sql_writes_rate_name_to_db`

```python
    def test_tag_rate_sql_writes_rate_name_to_db(self):
        """After populate_tag_usage_costs with tag_rate_names, line items have rate_name."""
        # Setup tag rates and source data ...
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_tag_usage_costs(
                infrastructure_rates, supplementary_rates,
                self.start_date, self.end_date, self.cluster_id,
                tag_rate_names={"cpu_core_usage_per_hour": {"workload": "JBoss subscription"}},
            )
        with schema_context(self.schema):
            tag_rows = OCPUsageLineItemDailySummary.objects.filter(
                monthly_cost_type="Tag",
                usage_start__gte=self.start_date,
                cluster_id=self.cluster_id,
            )
            for row in tag_rows:
                self.assertEqual(row.cost_model_rate_name, "JBoss subscription")
```

**Fails because:** SQL template and accessor don't handle `rate_name`.

### GREEN phase

1. Add `infrastructure_rates_by_name`, `supplementary_rates_by_name`, `tag_rate_names` properties to `CostModelDBAccessor` → T3.1–T3.5 pass
2. Add `name` to `metric_to_tag_params_map` entries → T3.5 passes
3. Refactor `_update_monthly_cost()` to iterate `rates_by_name` → T3.7, T3.8 pass
4. Pass `tag_rate_names` through updater → T3.9 passes
5. Add `rate_name` parameter to all `populate_*` methods → T3.10–T3.12 pass
6. Update SQL templates (tag rates, monthly rates) → T3.13, T3.14 pass

### REFACTOR phase

- Ensure `infrastructure_rates_by_name` is only computed once (cache as `@cached_property` or in `__init__`)
- Verify all existing tests still pass (no breaking changes to old properties)

---

## PR 4: Tiered Usage Rate Refactoring

**Files under test:**
- `koku/masu/database/ocp_report_db_accessor.py`
- `koku/masu/database/sql/openshift/cost_model/usage_costs.sql`

**Test file:** `koku/masu/test/database/test_ocp_report_db_accessor.py`

### RED phase

#### T4.1 `test_populate_usage_costs_by_name_creates_per_rate_rows`

```python
class PopulateUsageCostsByNameTest(MasuTestCase):
    """Tests for the per-rate usage cost method."""

    def test_populate_usage_costs_by_name_creates_per_rate_rows(self):
        """Each rate entry produces rows with distinct cost_model_rate_name."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU charge"},
            {"metric": "memory_gb_usage_per_hour", "value": Decimal("0.03"), "name": "Memory charge"},
        ]
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure", rates_by_name, "cpu",
                self.start_date, self.end_date, self.provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            names = set(
                OCPUsageLineItemDailySummary.objects.filter(
                    cost_model_rate_type="Infrastructure",
                    monthly_cost_type__isnull=True,
                    usage_start__gte=self.start_date,
                ).values_list("cost_model_rate_name", flat=True).distinct()
            )
        self.assertEqual(names, {"CPU charge", "Memory charge"})
```

**Fails because:** `populate_usage_costs_by_name` method doesn't exist.

#### T4.2 `test_populate_usage_costs_by_name_deletes_once_not_per_rate`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.delete_line_item_daily_summary_entries_for_date_range_raw")
    def test_populate_usage_costs_by_name_deletes_once_not_per_rate(self, mock_delete):
        """Deletion happens once before the loop, not per rate."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
            {"metric": "memory_gb_usage_per_hour", "value": Decimal("0.03"), "name": "Memory"},
            {"metric": "storage_gb_usage_per_month", "value": Decimal("0.01"), "name": "Storage"},
        ]
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure", rates_by_name, "cpu",
                self.start_date, self.end_date, self.provider_uuid,
                self.report_period_id,
            )
        self.assertEqual(mock_delete.call_count, 1)
```

**Fails because:** Method doesn't exist.

#### T4.3 `test_multiple_rates_same_metric_produce_separate_rows`

```python
    def test_multiple_rates_same_metric_produce_separate_rows(self):
        """Two CPU rates with different names produce two sets of rows."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "Base CPU"},
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.03"), "name": "Premium CPU"},
        ]
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure", rates_by_name, "cpu",
                self.start_date, self.end_date, self.provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            base_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_name="Base CPU",
                cost_model_rate_type="Infrastructure",
                monthly_cost_type__isnull=True,
            ).count()
            premium_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_name="Premium CPU",
                cost_model_rate_type="Infrastructure",
                monthly_cost_type__isnull=True,
            ).count()
        self.assertGreater(base_rows, 0)
        self.assertGreater(premium_rows, 0)
        self.assertEqual(base_rows, premium_rows)
```

**Fails because:** Method doesn't exist.

#### T4.4 `test_populate_usage_costs_by_name_empty_rates_deletes_only`

```python
    def test_populate_usage_costs_by_name_empty_rates_deletes_only(self):
        """Empty rates list deletes existing rows and inserts nothing."""
        # First create some rows
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
        ]
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure", rates_by_name, "cpu",
                self.start_date, self.end_date, self.provider_uuid,
                self.report_period_id,
            )
        # Now call with empty rates
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure", [], "cpu",
                self.start_date, self.end_date, self.provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            count = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="Infrastructure",
                monthly_cost_type__isnull=True,
                usage_start__gte=self.start_date,
            ).count()
        self.assertEqual(count, 0)
```

**Fails because:** Method doesn't exist.

#### T4.5 `test_zero_value_rate_skipped`

```python
    def test_zero_value_rate_skipped(self):
        """A rate with value=0 does not produce rows."""
        rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
            {"metric": "memory_gb_usage_per_hour", "value": Decimal("0"), "name": "Memory (zero)"},
        ]
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_usage_costs_by_name(
                "Infrastructure", rates_by_name, "cpu",
                self.start_date, self.end_date, self.provider_uuid,
                self.report_period_id,
            )
        with schema_context(self.schema):
            names = set(
                OCPUsageLineItemDailySummary.objects.filter(
                    cost_model_rate_type="Infrastructure",
                    monthly_cost_type__isnull=True,
                    usage_start__gte=self.start_date,
                ).values_list("cost_model_rate_name", flat=True).distinct()
            )
        self.assertEqual(names, {"CPU"})
        self.assertNotIn("Memory (zero)", names)
```

**Fails because:** Method doesn't exist.

#### T4.6 `test_usage_costs_sql_has_rate_name_column`

```python
    def test_usage_costs_sql_has_rate_name_column(self):
        """usage_costs.sql INSERT includes cost_model_rate_name."""
        import pkgutil
        sql = pkgutil.get_data("masu.database", "sql/openshift/cost_model/usage_costs.sql")
        sql_text = sql.decode("utf-8")
        self.assertIn("cost_model_rate_name", sql_text)
```

**Fails because:** SQL template not yet modified.

#### T4.7 `test_updater_calls_populate_usage_costs_by_name`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_usage_costs_by_name")
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor.populate_usage_costs")
    @patch("masu.processor.ocp.ocp_cost_model_cost_updater.CostModelDBAccessor")
    def test_updater_calls_populate_usage_costs_by_name(self, mock_accessor_cls, mock_old, mock_new):
        """_update_usage_costs calls populate_usage_costs_by_name (not the old method)."""
        mock_accessor = mock_accessor_cls.return_value.__enter__.return_value
        mock_accessor.infrastructure_rates = {"cpu_core_usage_per_hour": Decimal("0.05")}
        mock_accessor.supplementary_rates = {}
        mock_accessor.infrastructure_rates_by_name = [
            {"metric": "cpu_core_usage_per_hour", "value": Decimal("0.05"), "name": "CPU"},
        ]
        mock_accessor.supplementary_rates_by_name = []
        # ... other required mock attributes ...

        updater = OCPCostModelCostUpdater(self.schema, self.provider)
        updater._update_usage_costs(self.start_date, self.end_date)

        mock_new.assert_called()
        # Old method should NOT be called when rates_by_name is available
        mock_old.assert_not_called()
```

**Fails because:** `_update_usage_costs` still calls the old `populate_usage_costs`.

### GREEN phase

1. Add `cost_model_rate_name` to `usage_costs.sql` INSERT + SELECT, remove leading DELETE → T4.6 passes
2. Implement `populate_usage_costs_by_name()` with per-rate loop → T4.1–T4.5 pass
3. Update `_update_usage_costs()` in updater to call new method → T4.7 passes

### REFACTOR phase

- Extract the per-rate SQL params builder into a helper for readability
- Ensure the old `populate_usage_costs()` is still callable (backward compat during transition)
- Consider a temp table for `cte_node_cost` if performance benchmarks show regression

---

## PR 5: Distribution SQL Per-Rate-Name Tracking

**Files under test:**
- `koku/masu/database/sql/openshift/cost_model/distribute_cost/distribute_platform_cost.sql`
- (and all other distribution SQL files)
- `koku/masu/database/ocp_report_db_accessor.py`

**Test file:** `koku/masu/test/database/test_ocp_report_db_accessor.py`

### RED phase

#### T5.1 `test_distributed_rows_carry_rate_name_from_source`

```python
class DistributionRateNameTest(MasuTestCase):
    """Tests for per-rate-name distribution."""

    def test_distributed_rows_carry_rate_name_from_source(self):
        """After distribution, user-namespace rows carry cost_model_rate_name from the Platform source."""
        # Setup: create source data with cost_model_rate_name values on Platform rows
        # Run cost model application (PRs 3-4), then distribution
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_distributed_cost_sql(
                summary_range, self.provider_uuid,
                {"platform_cost": True, "worker_cost": False, "gpu_unallocated": False},
            )
        with schema_context(self.schema):
            distributed_rows = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                usage_start__gte=self.start_date,
            )
            rate_names = set(distributed_rows.values_list("cost_model_rate_name", flat=True))
            # Should contain rate names from Platform source + NULL (for cloud cost)
            self.assertTrue(len(rate_names) > 0)
            # Named rates should match what was in the Platform namespace
            named = {n for n in rate_names if n is not None}
            self.assertTrue(len(named) > 0)
```

**Fails because:** Distribution SQL doesn't track `cost_model_rate_name`.

#### T5.2 `test_distribution_sum_zero_per_rate_name`

```python
    def test_distribution_sum_zero_per_rate_name(self):
        """Sum of distributed_cost per cost_model_rate_name is zero (conservation)."""
        # Run full cost application + distribution
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_distributed_cost_sql(
                summary_range, self.provider_uuid,
                {"platform_cost": True, "worker_cost": False, "gpu_unallocated": False},
            )
        with schema_context(self.schema):
            sums = (
                OCPUsageLineItemDailySummary.objects.filter(
                    cost_model_rate_type__in=["platform_distributed"],
                    usage_start__gte=self.start_date,
                )
                .values("cost_model_rate_name")
                .annotate(total=Sum("distributed_cost"))
            )
            for entry in sums:
                self.assertAlmostEqual(float(entry["total"]), 0.0, places=6,
                    msg=f"Distribution not zero-sum for rate_name={entry['cost_model_rate_name']}")
```

**Fails because:** Distribution doesn't track per-rate-name and can't be zero-sum per name.

#### T5.3 `test_distribution_negation_grouped_by_rate_name`

```python
    def test_distribution_negation_grouped_by_rate_name(self):
        """Source namespace negation is per cost_model_rate_name."""
        # Setup: Platform has two rate names ("CPU charge", "Memory charge")
        # Run distribution
        with schema_context(self.schema):
            platform_negations = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                namespace__in=["openshift-kube-apiserver"],  # Platform namespace
                usage_start__gte=self.start_date,
            )
            negation_names = set(platform_negations.values_list("cost_model_rate_name", flat=True))
            # Should have separate negation rows for each rate name
            self.assertIn("CPU charge", negation_names)
            self.assertIn("Memory charge", negation_names)
            # Each negation should be negative
            for row in platform_negations:
                if row.cost_model_rate_name:
                    self.assertLess(row.distributed_cost, 0)
```

**Fails because:** Distribution doesn't do per-rate negation.

#### T5.4 `test_distribution_user_namespace_proportional`

```python
    def test_distribution_user_namespace_proportional(self):
        """User namespace distribution is proportional to usage, regardless of rate name."""
        # Setup: two user namespaces with different CPU usage
        # Run distribution with a Platform that has rate-named costs
        with schema_context(self.schema):
            ns_a_dist = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                namespace="namespace_a",
                cost_model_rate_name="CPU charge",
            ).aggregate(total=Sum("distributed_cost"))["total"]
            ns_b_dist = OCPUsageLineItemDailySummary.objects.filter(
                cost_model_rate_type="platform_distributed",
                namespace="namespace_b",
                cost_model_rate_name="CPU charge",
            ).aggregate(total=Sum("distributed_cost"))["total"]
            # Ratio should match their CPU usage ratio
            # (actual values depend on test data setup)
            self.assertGreater(ns_a_dist, 0)
            self.assertGreater(ns_b_dist, 0)
```

**Fails because:** Distribution SQL doesn't produce per-rate-name user rows.

#### T5.5 `test_all_five_distribution_types_track_rate_name`

```python
    def test_all_five_distribution_types_track_rate_name(self):
        """All distribution types (platform, worker, storage, network, GPU) carry rate_name."""
        distribution_types = [
            ("platform_cost", "platform_distributed"),
            ("worker_cost", "worker_unallocated_distributed"),
            # storage, network tested similarly
        ]
        for config_key, rate_type in distribution_types:
            with self.subTest(rate_type=rate_type):
                with OCPReportDBAccessor(self.schema) as acc:
                    acc.populate_distributed_cost_sql(
                        summary_range, self.provider_uuid, {config_key: True},
                    )
                with schema_context(self.schema):
                    rows = OCPUsageLineItemDailySummary.objects.filter(
                        cost_model_rate_type=rate_type,
                        usage_start__gte=self.start_date,
                    )
                    has_rate_names = rows.exclude(cost_model_rate_name__isnull=True).exists()
                    self.assertTrue(has_rate_names, f"{rate_type} should have rate-named rows")
```

**Fails because:** No distribution SQL tracks `cost_model_rate_name`.

### GREEN phase

1. Modify `distribute_platform_cost.sql` with split CTE pattern → T5.1, T5.2, T5.3, T5.4 pass for platform
2. Apply same pattern to all four other distribution SQL files → T5.5 passes
3. Apply to Trino/self-hosted GPU distribution SQL

### REFACTOR phase

- Verify the SQL is correct via `EXPLAIN ANALYZE` on a representative dataset
- Consider extracting the split-CTE pattern into a SQL fragment or template include if supported

---

## PR 6: Breakdown Summary Tables

**Files under test:**
- `koku/reporting/provider/ocp/models.py` (4 new models)
- `koku/masu/database/ocp_report_db_accessor.py` (population)
- New SQL files in `sql/openshift/ui_summary/`

**Test file:** `koku/masu/test/database/test_ocp_report_db_accessor.py`

### RED phase

#### T6.1 `test_breakdown_models_exist`

```python
class BreakdownSummaryTableTest(MasuTestCase):
    """Tests for breakdown summary table models."""

    def test_breakdown_models_exist(self):
        """All four breakdown models are importable."""
        from reporting.provider.ocp.models import (
            OCPCostBreakdownP,
            OCPCostBreakdownByProjectP,
            OCPCostBreakdownByNodeP,
            OCPVMBreakdownP,
        )
        self.assertTrue(hasattr(OCPCostBreakdownP, "cost_model_rate_name"))
        self.assertTrue(hasattr(OCPCostBreakdownByProjectP, "namespace"))
        self.assertTrue(hasattr(OCPCostBreakdownByNodeP, "node"))
        self.assertTrue(hasattr(OCPVMBreakdownP, "vm_name"))
```

**Fails because:** Models don't exist.

#### T6.2 `test_breakdown_tables_partitioned`

```python
    def test_breakdown_tables_partitioned(self):
        """Breakdown tables use RANGE partitioning on usage_start."""
        from reporting.provider.ocp.models import OCPCostBreakdownP
        self.assertEqual(OCPCostBreakdownP.PartitionInfo.partition_type, "RANGE")
        self.assertEqual(OCPCostBreakdownP.PartitionInfo.partition_cols, ["usage_start"])
```

**Fails because:** Model doesn't exist.

#### T6.3 `test_populate_breakdown_summary_tables`

```python
    def test_populate_breakdown_summary_tables(self):
        """Breakdown summary tables are populated from line item data."""
        # Prerequisites: line items with cost_model_rate_name set (from PRs 3-4)
        with OCPReportDBAccessor(self.schema) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPCostBreakdownP
            rows = OCPCostBreakdownP.objects.filter(
                usage_start__gte=self.start_date,
                source_uuid=self.provider_uuid,
            )
            self.assertTrue(rows.exists())
            rate_names = set(rows.values_list("cost_model_rate_name", flat=True))
            self.assertTrue(len(rate_names) > 0)
```

**Fails because:** Method and tables don't exist.

#### T6.4 `test_breakdown_by_project_includes_namespace`

```python
    def test_breakdown_by_project_includes_namespace(self):
        """OCPCostBreakdownByProjectP rows carry namespace dimension."""
        with OCPReportDBAccessor(self.schema) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPCostBreakdownByProjectP
            rows = OCPCostBreakdownByProjectP.objects.filter(
                usage_start__gte=self.start_date,
            )
            namespaces = set(rows.values_list("namespace", flat=True))
            self.assertTrue(len(namespaces) > 0)
```

**Fails because:** Model doesn't exist.

#### T6.5 `test_breakdown_cleanup_on_repopulate`

```python
    def test_breakdown_cleanup_on_repopulate(self):
        """Repopulating breakdown tables deletes old rows for the same date range and source."""
        with OCPReportDBAccessor(self.schema) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.provider_uuid)
        with schema_context(self.schema):
            count_1 = OCPCostBreakdownP.objects.filter(source_uuid=self.provider_uuid).count()
        # Repopulate
        with OCPReportDBAccessor(self.schema) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.provider_uuid)
        with schema_context(self.schema):
            count_2 = OCPCostBreakdownP.objects.filter(source_uuid=self.provider_uuid).count()
        self.assertEqual(count_1, count_2)
```

**Fails because:** Method doesn't exist.

#### T6.6 `test_breakdown_groups_by_rate_name`

```python
    def test_breakdown_groups_by_rate_name(self):
        """Breakdown table has separate rows for each (rate_type, rate_name) combination."""
        # Setup: line items with 2 different rate names for Infrastructure
        with OCPReportDBAccessor(self.schema) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.provider_uuid)
        with schema_context(self.schema):
            combos = list(
                OCPCostBreakdownP.objects.filter(
                    usage_start__gte=self.start_date,
                ).values("cost_model_rate_type", "cost_model_rate_name").distinct()
            )
            rate_type_name_pairs = {
                (c["cost_model_rate_type"], c["cost_model_rate_name"]) for c in combos
            }
            # Should have at least 2 distinct rate_name values for Infrastructure
            infra_names = {n for t, n in rate_type_name_pairs if t == "Infrastructure" and n}
            self.assertGreaterEqual(len(infra_names), 2)
```

**Fails because:** Table doesn't exist.

#### T6.7 `test_populate_ui_summary_calls_breakdown`

```python
    @patch("masu.database.ocp_report_db_accessor.OCPReportDBAccessor._populate_breakdown_summary_tables")
    def test_populate_ui_summary_calls_breakdown(self, mock_breakdown):
        """populate_ui_summary_tables also populates breakdown tables."""
        with OCPReportDBAccessor(self.schema) as acc:
            acc.populate_ui_summary_tables(summary_range, self.provider_uuid)
        mock_breakdown.assert_called_once()
```

**Fails because:** `populate_ui_summary_tables` doesn't call breakdown population.

#### T6.8 `test_vm_breakdown_populates_vm_name`

```python
    def test_vm_breakdown_populates_vm_name(self):
        """OCPVMBreakdownP rows include vm_name extracted from labels."""
        # Prerequisite: line items with data_source='Pod' and
        # all_labels containing 'vm_kubevirt_io_name'
        with OCPReportDBAccessor(self.schema) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPVMBreakdownP
            rows = OCPVMBreakdownP.objects.filter(
                usage_start__gte=self.start_date,
                source_uuid=self.provider_uuid,
            )
            vm_names = set(rows.values_list("vm_name", flat=True))
            non_null_names = {n for n in vm_names if n is not None}
            self.assertTrue(len(non_null_names) > 0, "VM breakdown should have vm_name values")
```

**Fails because:** `OCPVMBreakdownP` table doesn't exist.

#### T6.9 `test_breakdown_includes_tag_cost_type_rows`

```python
    def test_breakdown_includes_tag_cost_type_rows(self):
        """Breakdown summary includes rows with monthly_cost_type='Tag' (tag-based rates)."""
        # Setup: line items with monthly_cost_type='Tag' and cost_model_rate_name set
        with schema_context(self.schema):
            baker.make(
                OCPUsageLineItemDailySummary,
                usage_start=self.start_date,
                monthly_cost_type="Tag",
                cost_model_rate_type="Infrastructure",
                cost_model_rate_name="JBoss subscription",
                cost_model_cpu_cost=Decimal("40.00"),
                source_uuid=self.provider_uuid,
                cluster_id=self.cluster_id,
            )
        with OCPReportDBAccessor(self.schema) as acc:
            acc._populate_breakdown_summary_tables(summary_range, self.provider_uuid)
        with schema_context(self.schema):
            from reporting.provider.ocp.models import OCPCostBreakdownP
            rows = OCPCostBreakdownP.objects.filter(
                cost_model_rate_name="JBoss subscription",
                source_uuid=self.provider_uuid,
            )
            self.assertTrue(rows.exists(), "Tag-based rate rows must appear in breakdown summary")
```

**Fails because:** Breakdown table doesn't exist.

### GREEN phase

1. Create 4 Django models with `PartitionInfo` → T6.1, T6.2 pass
2. Write migration → tables exist in DB
3. Write 4 SQL files for breakdown population → T6.3, T6.4, T6.6, T6.8, T6.9 pass
4. Add `_populate_breakdown_summary_tables()` to accessor → T6.5 passes
5. Call it from `populate_ui_summary_tables()` → T6.7 passes

### REFACTOR phase

- Verify SQL files follow exact same column order as the Django model for maintainability
- Consider whether `OCPVMBreakdownP` needs `cost_category` (verify VM summary SQL)

---

## PR 7: API Layer

**Files under test:**
- `koku/api/report/ocp/query_handler.py`
- `koku/api/report/ocp/serializers.py`
- `koku/api/report/ocp/provider_map.py`
- `koku/api/report/all/openshift/query_handler.py`
- OCP-on-cloud query handlers

**Test files:**
- `koku/api/report/test/ocp/test_ocp_query_handler.py`
- `koku/api/report/test/ocp/test_serializers.py`
- `koku/api/report/test/ocp/view/test_views.py`

### RED phase — Serializer

#### T7.1 `test_breakdown_limit_accepted`

```python
class OCPCostSerializerBreakdownTest(TestCase):
    """Tests for breakdown_limit query parameter."""

    def test_breakdown_limit_accepted(self):
        """breakdown_limit is accepted as an integer parameter."""
        params = {
            "breakdown_limit": 5,
            "filter": {"resolution": "monthly", "time_scope_value": "-1", "time_scope_units": "month"},
        }
        serializer = OCPCostQueryParamSerializer(data=params)
        self.assertTrue(serializer.is_valid())
```

**Fails because:** `breakdown_limit` not in serializer.

#### T7.2 `test_breakdown_limit_rejects_zero`

```python
    def test_breakdown_limit_rejects_zero(self):
        """breakdown_limit < 1 is rejected."""
        params = {
            "breakdown_limit": 0,
            "filter": {"resolution": "monthly", "time_scope_value": "-1", "time_scope_units": "month"},
        }
        serializer = OCPCostQueryParamSerializer(data=params)
        self.assertFalse(serializer.is_valid())
```

#### T7.3 `test_breakdown_limit_rejects_over_100`

```python
    def test_breakdown_limit_rejects_over_100(self):
        """breakdown_limit > 100 is rejected."""
        params = {
            "breakdown_limit": 101,
            "filter": {"resolution": "monthly", "time_scope_value": "-1", "time_scope_units": "month"},
        }
        serializer = OCPCostQueryParamSerializer(data=params)
        self.assertFalse(serializer.is_valid())
```

### RED phase — Query Handler: Breakdown in Response

#### T7.4 `test_cost_response_includes_usage_breakdown`

```python
class OCPCostQueryHandlerBreakdownTest(IamTestCase):
    """Tests for breakdown data in API response."""

    def test_cost_response_includes_usage_breakdown(self):
        """OCP cost response includes 'breakdown' array on usage cost."""
        # Setup: breakdown summary tables populated with rate-named data
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        self.assertIn("breakdown", usage)
        breakdown = usage["breakdown"]
        self.assertIsInstance(breakdown, list)
        if breakdown:
            entry = breakdown[0]
            self.assertIn("name", entry)
            self.assertIn("source", entry)
            self.assertIn("value", entry)
            self.assertIn("units", entry)
```

**Fails because:** Query handler doesn't add `breakdown`.

#### T7.5 `test_cost_response_includes_overhead_breakdown`

```python
    def test_cost_response_includes_overhead_breakdown(self):
        """OCP cost response includes 'breakdown' on overhead costs (platform_distributed)."""
        # Setup: distribution + breakdown tables populated
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        if "platform_distributed" in cost:
            pd = cost["platform_distributed"]
            self.assertIn("breakdown", pd)
```

**Fails because:** No overhead breakdown attached.

#### T7.6 `test_breakdown_limit_limits_entries`

```python
    def test_breakdown_limit_limits_entries(self):
        """breakdown_limit=2 returns top 2 + 'Other' aggregation."""
        # Setup: breakdown data with 5+ rate names
        url = reverse("reports-openshift-costs") + "?breakdown_limit=2"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        breakdown = usage.get("breakdown", [])
        # At most 3 entries: 2 top + "Other"
        self.assertLessEqual(len(breakdown), 3)
        if len(breakdown) == 3:
            self.assertEqual(breakdown[-1]["name"], "Other")
```

**Fails because:** No breakdown_limit support.

#### T7.7 `test_no_breakdown_limit_returns_full`

```python
    def test_no_breakdown_limit_returns_full(self):
        """Without breakdown_limit, full breakdown is returned."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        breakdown = usage.get("breakdown", [])
        # No "Other" entry when unlimited
        other_entries = [e for e in breakdown if e.get("name") == "Other"]
        self.assertEqual(len(other_entries), 0)
```

#### T7.8 `test_per_row_breakdown_attached`

```python
    def test_per_row_breakdown_attached(self):
        """Each date row in data array has breakdown on its cost categories."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()["data"]
        for date_entry in data:
            # date_entry structure varies by group-by, check nested cost objects
            values = date_entry.get("values", date_entry.get("clusters", []))
            for val in values if isinstance(values, list) else []:
                cost = val.get("cost", {})
                usage = cost.get("usage", {})
                if usage.get("value", 0) > 0:
                    self.assertIn("breakdown", usage)
```

**Fails because:** Per-row breakdown not attached.

#### T7.8b `test_per_row_breakdown_respects_limit`

```python
    def test_per_row_breakdown_respects_limit(self):
        """Per-row breakdown also applies breakdown_limit top-N with 'Other'."""
        # Setup: breakdown data with 5+ rate names per date
        url = reverse("reports-openshift-costs") + "?breakdown_limit=2"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()["data"]
        for date_entry in data:
            values = date_entry.get("values", date_entry.get("clusters", []))
            for val in values if isinstance(values, list) else []:
                cost = val.get("cost", {})
                usage = cost.get("usage", {})
                breakdown = usage.get("breakdown", [])
                if breakdown:
                    # At most 3 entries: 2 top + "Other"
                    self.assertLessEqual(len(breakdown), 3)
```

**Fails because:** Per-row breakdown doesn't apply `breakdown_limit`.

#### T7.9 `test_null_named_entries_aggregated_as_cloud_cost`

```python
    def test_null_named_entries_aggregated_as_cloud_cost(self):
        """NULL-named overhead entries appear as 'Cloud cost' in breakdown."""
        # Setup: distribution data with both named and NULL rate names
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        if "platform_distributed" in cost:
            breakdown = cost["platform_distributed"].get("breakdown", [])
            cloud_entries = [e for e in breakdown if e.get("source") == "cloud"]
            if cloud_entries:
                self.assertEqual(cloud_entries[0]["name"], "Cloud cost")
```

#### T7.10 `test_existing_response_fields_unchanged`

```python
    def test_existing_response_fields_unchanged(self):
        """Existing cost fields (value, units) are not altered by breakdown addition."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        # These fields must still exist and be numeric
        for key in ["total", "usage", "raw"]:
            if key in cost:
                self.assertIn("value", cost[key])
                self.assertIn("units", cost[key])
```

**Passes already** — regression guard.

### RED phase — CSV

#### T7.11 `test_csv_with_breakdown_includes_rate_name_column`

```python
    def test_csv_with_breakdown_includes_rate_name_column(self):
        """CSV export with breakdown_limit includes cost_model_rate_name column."""
        url = reverse("reports-openshift-costs") + "?breakdown_limit=10"
        client = APIClient()
        response = client.get(url, HTTP_ACCEPT="text/csv", **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        content = response.content.decode("utf-8")
        self.assertIn("cost_model_rate_name", content)
```

**Fails because:** CSV path doesn't handle breakdown.

#### T7.12 `test_csv_without_breakdown_no_rate_name_column`

```python
    def test_csv_without_breakdown_no_rate_name_column(self):
        """CSV export without breakdown_limit does not include cost_model_rate_name."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, HTTP_ACCEPT="text/csv", **self.headers)
        content = response.content.decode("utf-8")
        self.assertNotIn("cost_model_rate_name", content)
```

**Passes already** — regression guard.

### RED phase — OCP-on-Cloud

#### T7.13 `test_ocp_aws_includes_breakdown`

```python
    def test_ocp_aws_includes_breakdown(self):
        """OCP-on-AWS cost response includes breakdown from OCP breakdown tables."""
        url = reverse("reports-openshift-aws-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        usage = cost.get("usage", {})
        # If there's usage cost, breakdown should be available
        if usage.get("value", 0) > 0:
            self.assertIn("breakdown", usage)
```

**Fails because:** OCP-on-cloud handlers don't use `BreakdownMixin`.

### RED phase — All required perspectives (PRD)

#### T7.13b `test_provider_map_has_breakdown_views`

```python
    def test_provider_map_has_breakdown_views(self):
        """Provider map defines breakdown_views for costs, costs_by_project, and VMs."""
        from api.report.ocp.provider_map import OCPProviderMap
        from reporting.provider.ocp.models import (
            OCPCostBreakdownP, OCPCostBreakdownByProjectP,
            OCPCostBreakdownByNodeP, OCPVMBreakdownP,
        )

        provider_map = OCPProviderMap(self.provider, "costs")
        self.assertIn("costs", provider_map.breakdown_views)
        self.assertEqual(provider_map.breakdown_views["costs"]["default"], OCPCostBreakdownP)
        self.assertEqual(
            provider_map.breakdown_views["costs"][("node",)], OCPCostBreakdownByNodeP
        )

        self.assertIn("costs_by_project", provider_map.breakdown_views)
        self.assertEqual(
            provider_map.breakdown_views["costs_by_project"]["default"],
            OCPCostBreakdownByProjectP,
        )

        self.assertIn("virtual_machines", provider_map.breakdown_views)
        self.assertEqual(
            provider_map.breakdown_views["virtual_machines"]["default"], OCPVMBreakdownP
        )
```

**Fails because:** Provider map doesn't define `breakdown_views`.

#### T7.13f `test_get_breakdown_table_resolves_by_group_by`

```python
    def test_get_breakdown_table_resolves_by_group_by(self):
        """Query handler selects correct breakdown table based on group-by."""
        from reporting.provider.ocp.models import (
            OCPCostBreakdownP, OCPCostBreakdownByNodeP,
        )

        # No group-by → default breakdown table
        url = "?filter[time_scope_value]=-1&filter[time_scope_units]=month"
        handler = OCPReportQueryHandler(url, self.tenant, **self.query_params)
        self.assertEqual(handler._get_breakdown_table(), OCPCostBreakdownP)

        # Node group-by → node breakdown table
        url = "?filter[time_scope_value]=-1&filter[time_scope_units]=month&group_by[node]=*"
        handler = OCPReportQueryHandler(url, self.tenant, **self.query_params)
        self.assertEqual(handler._get_breakdown_table(), OCPCostBreakdownByNodeP)
```

**Fails because:** `_get_breakdown_table()` method doesn't exist yet.

#### T7.13c `test_costs_by_project_includes_breakdown`

```python
    def test_costs_by_project_includes_breakdown(self):
        """OCP costs_by_project response includes breakdown per project."""
        url = reverse("reports-openshift-costs") + "?group_by[project]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        # Check total has breakdown
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        self.assertIn("breakdown", usage)
        # Check per-project rows in data have breakdown
        for date_entry in data.get("data", []):
            for project in date_entry.get("projects", []):
                cost = project.get("cost", {})
                proj_usage = cost.get("usage", {})
                if proj_usage.get("value", 0) > 0:
                    self.assertIn("breakdown", proj_usage)
```

**Fails because:** Query handler doesn't attach breakdown.

#### T7.13d `test_node_group_by_includes_breakdown`

```python
    def test_node_group_by_includes_breakdown(self):
        """OCP costs grouped by node include breakdown."""
        url = reverse("reports-openshift-costs") + "?group_by[node]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        usage = total.get("cost", {}).get("usage", {})
        self.assertIn("breakdown", usage)
```

**Fails because:** Query handler doesn't attach breakdown.

#### T7.13e `test_vm_view_includes_breakdown`

```python
    def test_vm_view_includes_breakdown(self):
        """OCP virtual machine view includes breakdown."""
        url = reverse("reports-openshift-virtual-machines")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data.get("meta", {}).get("total", {})
        cost = total.get("cost", {})
        usage = cost.get("usage", {})
        if usage.get("value", 0) > 0:
            self.assertIn("breakdown", usage)
```

**Fails because:** VM query handler doesn't use BreakdownMixin.

### RED phase — Tag group-by

#### T7.14 `test_tag_group_by_includes_breakdown`

```python
    def test_tag_group_by_includes_breakdown(self):
        """Tag group-by query includes per-rate breakdown."""
        url = reverse("reports-openshift-costs") + "?group_by[tag:app]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        # Verify breakdown is present on tag-grouped entries
        for date_entry in data.get("data", []):
            for tag_group in date_entry.get("app", []):
                cost = tag_group.get("cost", {})
                usage = cost.get("usage", {})
                if usage.get("value", 0) > 0:
                    self.assertIn("breakdown", usage)
```

**Fails because:** Tag group-by doesn't query breakdown.

### GREEN phase

1. Add `breakdown_limit` to serializer → T7.1–T7.3 pass
2. Add `breakdown_views` to provider map, `_get_breakdown_table()` to query handler → T7.13b, T7.13f pass
3. Add `_get_breakdown_data()`, `_build_usage_breakdown()`, `_build_overhead_breakdown()`, `_apply_breakdown_limit()` to query handler → T7.4–T7.7, T7.9 pass
4. Add `_attach_breakdown_to_data_rows()` with `breakdown_limit` passthrough → T7.8, T7.8b pass
5. Add CSV breakdown path in `execute_query()` → T7.11 passes
6. Add `BreakdownMixin` and use in OCP-on-cloud handlers → T7.13 passes
7. Add `_get_breakdown_data_for_tags()` → T7.14 passes

### REFACTOR phase

- Extract common breakdown formatting logic to avoid duplication between total and per-row
- Ensure `_apply_breakdown_limit` is reused consistently for both total and per-row
- Profile the secondary breakdown query to ensure it stays within the 30-second API timeout

---

## PR 8: Trino and Self-Hosted SQL Paths

**Files under test:**
- All SQL files in `trino_sql/openshift/cost_model/`
- All SQL files in `self_hosted_sql/openshift/cost_model/`

**Test file:** `koku/masu/test/database/test_ocp_report_db_accessor.py` (or new file for SQL content tests)

### RED phase

#### T8.1 `test_trino_vm_sql_has_rate_name`

```python
class TrinoSelfHostedRateNameTest(TestCase):
    """Tests that Trino and self-hosted SQL templates include cost_model_rate_name."""

    def _assert_sql_has_rate_name(self, path):
        """Helper: assert that a SQL file includes cost_model_rate_name."""
        import pkgutil
        sql = pkgutil.get_data("masu.database", path)
        sql_text = sql.decode("utf-8")
        self.assertIn("cost_model_rate_name", sql_text,
                       f"{path} missing cost_model_rate_name")

    def test_trino_vm_sql_files_have_rate_name(self):
        """All Trino VM SQL templates include cost_model_rate_name."""
        trino_files = [
            "trino_sql/openshift/cost_model/hourly_cost_virtual_machine.sql",
            "trino_sql/openshift/cost_model/hourly_vm_core.sql",
            "trino_sql/openshift/cost_model/monthly_vm_core.sql",
            "trino_sql/openshift/cost_model/hourly_cost_vm_tag_based.sql",
            "trino_sql/openshift/cost_model/hourly_vm_core_tag_based.sql",
            "trino_sql/openshift/cost_model/monthly_vm_core_tag_based.sql",
            "trino_sql/openshift/cost_model/monthly_project_tag_based.sql",
            "trino_sql/openshift/cost_model/monthly_cost_gpu.sql",
        ]
        for path in trino_files:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)
```

**Fails because:** SQL files not yet modified.

#### T8.2 `test_self_hosted_vm_sql_files_have_rate_name`

```python
    def test_self_hosted_vm_sql_files_have_rate_name(self):
        """All self-hosted VM SQL templates include cost_model_rate_name."""
        self_hosted_files = [
            "self_hosted_sql/openshift/cost_model/hourly_cost_virtual_machine.sql",
            "self_hosted_sql/openshift/cost_model/hourly_vm_core.sql",
            "self_hosted_sql/openshift/cost_model/monthly_vm_core.sql",
            "self_hosted_sql/openshift/cost_model/hourly_cost_vm_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/hourly_vm_core_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/monthly_vm_core_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/monthly_project_tag_based.sql",
            "self_hosted_sql/openshift/cost_model/monthly_cost_gpu.sql",
        ]
        for path in self_hosted_files:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)
```

**Fails because:** SQL files not yet modified.

#### T8.3 `test_cloud_sql_files_have_rate_name`

```python
    def test_cloud_sql_files_have_rate_name(self):
        """Cloud PostgreSQL SQL templates include cost_model_rate_name."""
        cloud_files = [
            "sql/openshift/cost_model/usage_costs.sql",
            "sql/openshift/cost_model/infrastructure_tag_rates.sql",
            "sql/openshift/cost_model/supplementary_tag_rates.sql",
            "sql/openshift/cost_model/default_infrastructure_tag_rates.sql",
            "sql/openshift/cost_model/default_supplementary_tag_rates.sql",
            "sql/openshift/cost_model/monthly_cost_cluster_and_node.sql",
            "sql/openshift/cost_model/monthly_cost_persistentvolumeclaim.sql",
            "sql/openshift/cost_model/monthly_cost_virtual_machine.sql",
            "sql/openshift/cost_model/node_cost_by_tag.sql",
            "sql/openshift/cost_model/monthly_cost_persistentvolumeclaim_by_tag.sql",
        ]
        for path in cloud_files:
            with self.subTest(path=path):
                self._assert_sql_has_rate_name(path)
```

**Fails because:** SQL files not yet modified (covered by PRs 3-4 for cloud, PR 8 for Trino/self-hosted).

### GREEN phase

1. Add `cost_model_rate_name` to all Trino SQL files → T8.1 passes
2. Add `cost_model_rate_name` to all self-hosted SQL files → T8.2 passes
3. T8.3 should already pass after PRs 3-4 (cloud SQL modified there)

### REFACTOR phase

- Verify Trino and self-hosted SQL produce identical `cost_model_rate_name` values for the same input data
- Consider a shared SQL fragment for the INSERT column list if the template engine supports it

---

## End-to-End Integration Tests

These tests run the full pipeline from cost model creation through API response. They require all 8 PRs to be merged.

**Test file:** `koku/api/report/test/ocp/test_cost_breakdown_e2e.py` (new)

### T-E2E.1 `test_full_pipeline_rate_names_in_api_response`

```python
class CostBreakdownE2ETest(IamTestCase):
    """End-to-end test: cost model → cost application → breakdown → API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Create cost model with named rates
        cls.cost_model_data = {
            "name": "E2E Test Cost Model",
            "source_type": Provider.PROVIDER_OCP,
            "distribution": "cpu",
            "markup": {"value": 10, "unit": "percent"},
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
                    "cost_type": "Supplementary",
                },
                {
                    "name": "Node monthly",
                    "metric": {"name": "node_cost_per_month"},
                    "tiered_rates": [{"value": 100, "unit": "USD"}],
                    "cost_type": "Infrastructure",
                },
            ],
            "currency": "USD",
        }
        # Create cost model, load OCP data, run pipeline

    def test_full_pipeline_rate_names_in_api_response(self):
        """API response contains breakdown with all three rate names."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data["meta"]["total"]
        usage = total["cost"]["usage"]
        self.assertIn("breakdown", usage)
        breakdown_names = {e["name"] for e in usage["breakdown"]}
        self.assertIn("CPU charge", breakdown_names)
        self.assertIn("Memory charge", breakdown_names)
        self.assertIn("Node monthly", breakdown_names)
```

### T-E2E.2 `test_full_pipeline_overhead_breakdown`

```python
    def test_full_pipeline_overhead_breakdown(self):
        """Platform distributed cost has per-rate-name breakdown."""
        url = reverse("reports-openshift-costs") + "?group_by[project]=*"
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        total = data["meta"]["total"]
        cost = total["cost"]
        if "platform_distributed" in cost:
            pd = cost["platform_distributed"]
            self.assertIn("breakdown", pd)
            breakdown = pd["breakdown"]
            # Should have named rate entries and possibly "Cloud cost"
            self.assertTrue(len(breakdown) > 0)
            for entry in breakdown:
                self.assertIn("name", entry)
                self.assertIn("source", entry)
                self.assertIn(entry["source"], ("rate", "cloud"))
```

### T-E2E.3 `test_full_pipeline_csv_breakdown`

```python
    def test_full_pipeline_csv_breakdown(self):
        """CSV export with breakdown_limit includes rate name rows."""
        url = reverse("reports-openshift-costs") + "?breakdown_limit=10"
        client = APIClient()
        response = client.get(url, HTTP_ACCEPT="text/csv", **self.headers)
        content = response.content.decode("utf-8")
        self.assertIn("cost_model_rate_name", content)
        self.assertIn("CPU charge", content)
        self.assertIn("Memory charge", content)
```

### T-E2E.4 `test_full_pipeline_backward_compatible`

```python
    def test_full_pipeline_backward_compatible(self):
        """API response without breakdown_limit is backward compatible with existing schema."""
        url = reverse("reports-openshift-costs")
        client = APIClient()
        response = client.get(url, **self.headers)
        data = response.json()
        # Verify structural keys exist (backward compat)
        self.assertIn("meta", data)
        self.assertIn("data", data)
        self.assertIn("total", data["meta"])
        cost = data["meta"]["total"]["cost"]
        for key in ["total", "usage"]:
            if key in cost:
                self.assertIn("value", cost[key])
                self.assertIn("units", cost[key])
```

---

## PRD vs DD: Explicitly Out of Scope for Phase 1

The PRD acceptance criteria (line 552) lists "Markup breakdown reflects proportional attribution by the entity's infrastructure raw cost composition" under Phase 1. However, the PRD's own implementation notes (line 472) state: "markup breakdown is deferred to Phase 2" because markup applies to cloud raw cost, which doesn't have per-service granularity until Phase 2. The DD follows this (Section 13.3: "markup breakdown deferred"). For pure on-prem OCP, markup is $0.

**No markup breakdown tests are included in this plan.** The PRD acceptance criteria should be updated to move this item to Phase 2.

---

## Test Execution Order

The tests should be written and executed in this order, matching the PR merge sequence:

```
PR 1 tests (T1.1–T1.11)          → implements rate name field
PR 2 tests (T2.1–T2.3)           → implements line item column
                                  ↕ (parallel)
PR 3 tests (T3.1–T3.14, T3.9b-c) → rate name threading
PR 4 tests (T4.1–T4.7)           → tiered rate refactoring
                                  ↕ (parallel with PR 6)
PR 5 tests (T5.1–T5.5)           → distribution per-rate-name
PR 6 tests (T6.1–T6.9)           → breakdown summary tables
                                  ↓
PR 7 tests (T7.1–T7.14, T7.8b, T7.13b-f) → API layer
PR 8 tests (T8.1–T8.3)           → Trino + self-hosted SQL
                                  ↓
E2E tests (T-E2E.1–T-E2E.4)      → full pipeline verification
```

---

## Test Data Requirements

| PR | Test Data Needed | Setup Method |
|----|-----------------|-------------|
| PR 1 | CostModel with rates JSON | Inline dicts, `CostModelSerializer` |
| PR 2 | `OCPUsageLineItemDailySummary` rows | `baker.make()` |
| PR 3 | CostModel + line items + report period | `ReportObjectCreator.create_cost_model()`, baker recipes |
| PR 4 | Raw OCP line items (Pod, Storage data) | Baker recipes (`ocp_usage_pod`, `ocp_usage_storage`) |
| PR 5 | Cost-model-applied rows with rate names, Platform/Worker namespaces | Full pipeline: baker recipes → `update_cost_model_costs()` |
| PR 6 | Line items with `cost_model_rate_name` populated | Baker recipes → cost application (PRs 3-4) |
| PR 7 | Breakdown summary tables populated | Full pipeline through PR 6 |
| PR 8 | SQL file content only (no DB data) | `pkgutil.get_data()` |
| E2E | Full stack: cost model, OCP data, cost application, distribution, breakdown tables | `ModelBakeryDataLoader` + named-rate cost model |

---

## Summary

| PR | Test Count | Focus |
|----|-----------|-------|
| PR 1 | 11 | Serializer validation, migration logic |
| PR 2 | 3 | Model field, column existence |
| PR 3 | 16 | Accessor properties, updater threading (incl. monthly tag + GPU), SQL params, DB writes |
| PR 4 | 7 | Per-rate execution, multiple rates, delete-once, updater integration |
| PR 5 | 5 | Distribution rate-name tracking, conservation, negation |
| PR 6 | 9 | Model existence, population, cleanup, group-by, VM names, tag cost type |
| PR 7 | 21 | Serializer params, JSON breakdown, CSV, OCP-on-cloud, tag/node/project/VM perspectives, provider map, breakdown_views, per-row limit |
| PR 8 | 3 | SQL file content verification |
| E2E | 4 | Full pipeline, backward compat |
| **Total** | **79** | |
