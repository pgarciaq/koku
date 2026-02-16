#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the CostModelDBAccessor utility object."""
import random
from decimal import Decimal

from django_tenants.utils import schema_context

from api.metrics import constants as metric_constants
from api.models import Provider
from api.report.test.util.constants import OCP_ON_PREM_COST_MODEL
from cost_models.models import CostModel
from masu.database.cost_model_db_accessor import CostModelDBAccessor
from masu.test import MasuTestCase
from masu.test.database.helpers import ReportObjectCreator


def get_cost_type_defaults():
    return {
        metric: value["default_cost_type"] for metric, value in metric_constants.get_cost_model_metrics_map().items()
    }


def build_rates():
    """Returns rate_list to use to build cost model and mapping of expected values."""
    # Get defaults from constants.py
    cost_type_defaults = get_cost_type_defaults()
    mapping = {}
    rates = []
    metric_names = [
        "cpu_core_usage_per_hour",
        "memory_gb_usage_per_hour",
        "cpu_core_request_per_hour",
        "memory_gb_request_per_hour",
        "storage_gb_usage_per_month",
        "storage_gb_request_per_month",
        "node_cost_per_month",
    ]
    duplicates_per_cost_type = 2
    for metric_name in metric_names:
        for cost_type in ["Infrastructure", "Supplementary"]:
            mapping_total = []
            for idx in range(duplicates_per_cost_type):
                value = round(random.uniform(1, 5), 2)
                mapping_total.append(value)
                rate = {"metric": {"name": metric_name}, "tiered_rates": [{"value": value, "unit": "USD"}]}
                # For testing the default cost type
                if cost_type != cost_type_defaults.get(metric_name):
                    rate["cost_type"] = cost_type
                rates.append(rate)
            if mapping.get(metric_name):
                original_mapping = mapping[metric_name]
                original_mapping[cost_type] = sum(mapping_total)
            else:
                mapping[metric_name] = {cost_type: sum(mapping_total)}
    return rates, mapping


def parse_expected():
    rates_list = OCP_ON_PREM_COST_MODEL.get("rates")
    result = {"Infrastructure": {}, "Supplementary": {}}
    for rate in rates_list:
        name = rate["metric"]["name"]
        cost_type = rate["cost_type"]
        result[cost_type][name] = rate["tiered_rates"][0]["value"]
    return result


class CostModelDBAccessorTest(MasuTestCase):
    """Test Cases for the CostModelDBAccessor object."""

    def setUp(self):
        """Set up a test with database objects."""
        super().setUp()
        self.provider_uuid = self.ocp_provider_uuid

        self.rates, self.expected_value_rate_mapping = build_rates()
        self.markup = {"value": 10, "unit": "percent"}
        with schema_context(self.schema):
            self.cost_model = CostModel.objects.first()
        self.expected = parse_expected()

    def test_initializer(self):
        """Test initializer."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            self.assertEqual(cost_model_accessor.provider_uuid, self.provider_uuid)

    def test_get_rates(self):
        """Test get rates."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            cpu_usage_rate = cost_model_accessor.get_rates("cpu_core_usage_per_hour")
            self.assertEqual(type(cpu_usage_rate), dict)

            mem_usage_rate = cost_model_accessor.get_rates("memory_gb_usage_per_hour")
            self.assertEqual(type(mem_usage_rate), dict)

            cpu_request_rate = cost_model_accessor.get_rates("cpu_core_request_per_hour")
            self.assertEqual(type(cpu_request_rate), dict)

            mem_request_rate = cost_model_accessor.get_rates("memory_gb_request_per_hour")
            self.assertEqual(type(mem_request_rate), dict)

            storage_usage_rate = cost_model_accessor.get_rates("storage_gb_usage_per_month")
            self.assertEqual(type(storage_usage_rate), dict)

            storage_request_rate = cost_model_accessor.get_rates("storage_gb_request_per_month")
            self.assertEqual(type(storage_request_rate), dict)

            storage_request_rate = cost_model_accessor.get_rates("node_cost_per_month")
            self.assertEqual(type(storage_request_rate), dict)

            missing_rate = cost_model_accessor.get_rates("wrong_metric")
            self.assertIsNone(missing_rate)

    def test_markup(self):
        """Test to make sure markup dictionary is returned."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            markup = cost_model_accessor.markup
            self.assertEqual(markup, self.markup)

    def test_get_cost_model(self):
        """Test to make sure cost_model is gotten."""
        with schema_context(self.schema):
            model = CostModel.objects.filter(costmodelmap__provider_uuid=self.provider_uuid).first()
            uuid = model.uuid
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            self.assertEqual(cost_model_accessor.cost_model, model)
            self.assertEqual(cost_model_accessor.cost_model.uuid, uuid)

    def test_infrastructure_rates(self):
        """Test infrastructure rates property."""
        cost_type = "Infrastructure"
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            result_infra_rates = cost_model_accessor.infrastructure_rates
            for metric_name in result_infra_rates.keys():
                expected_value = self.expected[cost_type][metric_name]
                self.assertEqual(result_infra_rates[metric_name], expected_value)

    def test_supplementary_rates(self):
        """Test supplementary rates property."""
        cost_type = "Supplementary"
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            result_sup_rates = cost_model_accessor.supplementary_rates
            for metric_name in result_sup_rates.keys():
                expected_value = self.expected[cost_type][metric_name]
                self.assertEqual(result_sup_rates[metric_name], expected_value)

    def test_params_with_no_tag_params(self):
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            self.assertFalse(cost_model_accessor.metric_to_tag_params_map)


class CostModelDBAccessorTestNoRateOrMarkup(MasuTestCase):
    """Test Cases for the CostModelDBAccessor object."""

    def setUp(self):
        """Set up a test with database objects."""
        super().setUp()
        # Use an OCP provider that does not have a cost model. Any OCP-on-X OCP provider will do
        self.provider_uuid = self.ocpaws_provider_uuid
        self.creator = ReportObjectCreator(self.schema)
        self.cost_model = self.creator.create_cost_model(self.provider_uuid, Provider.PROVIDER_OCP)

    def test_initializer_no_rate_no_markup(self):
        """Test initializer."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            self.assertEqual(cost_model_accessor.provider_uuid, self.provider_uuid)

    def test_get_rates(self):
        """Test get rates."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            cpu_usage_rate = cost_model_accessor.get_rates("cpu_core_usage_per_hour")
            self.assertIsNone(cpu_usage_rate)


class CostModelDBAccessorNoCostModel(MasuTestCase):
    """Test Cases for the CostModelDBAccessor object."""

    def setUp(self):
        """Set up a test with database objects."""
        super().setUp()
        self.provider_uuid = "3c6e687e-1a09-4a05-970c-2ccf44b0952e"

    def test_get_rates_no_cost_model(self):
        """Test that get_rates returns empty dict when cost model does not exist."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            cpu_usage_rate = cost_model_accessor.get_rates("cpu_core_usage_per_hour")
            self.assertFalse(cpu_usage_rate)

    def test_markup_no_cost_model(self):
        """Test that markup returns empty dict when cost model does not exist."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            markup = cost_model_accessor.markup
            self.assertFalse(markup)

    def test_params_with_no_cost_model(self):
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            self.assertFalse(cost_model_accessor.metric_to_tag_params_map)


class CostModelDBAccessorTagRatesTest(MasuTestCase):
    """Test Cases for the CostModelDBAccessor object with tag rates."""

    KEY_VALUE_PAIRS = {
        "breakfast": ["pancakes", "waffles", "syrup"],
        "lunch": ["soup", "salad", "sandwich"],
        "dinner": ["steak", "chicken", "tacos"],
    }

    def build_tag_rates(self):
        """Returns rate_list to use to build cost model and mapping of expected values."""
        mapping = {}
        rates = []
        metric_names = [
            "cpu_core_usage_per_hour",
            "memory_gb_usage_per_hour",
            "cpu_core_request_per_hour",
            "memory_gb_request_per_hour",
            "storage_gb_usage_per_month",
            "storage_gb_request_per_month",
            "node_cost_per_month",
        ]
        mapping = {}
        for metric_name in metric_names:
            for cost_type in ["Infrastructure", "Supplementary"]:
                tag_key_dict = {}
                tag_vals_list = []
                default_other_keys = []
                tag_key = random.choice(list(self.KEY_VALUE_PAIRS))
                tag_vals = self.KEY_VALUE_PAIRS.get(tag_key)
                map_vals = {}
                for idx, tag_value_name in enumerate(tag_vals[0:2]):
                    value = round(random.uniform(1, 5), 2)
                    if idx == 0:
                        default = True
                        default_value = value
                    else:
                        default = False
                    default_other_keys.append(tag_value_name)
                    map_vals[tag_value_name] = value
                    val_dict = {"tag_value": tag_value_name, "value": value, "default": default}
                    tag_vals_list.append(val_dict)
                tag_key_dict = {"tag_key": tag_key, "tag_values": tag_vals_list}
                rate = {"metric": {"name": metric_name}, "tag_rates": tag_key_dict, "cost_type": cost_type}
                rates.append(rate)
                if mapping.get(metric_name):
                    existing_dict = mapping.get(metric_name)
                    existing_dict[cost_type] = {
                        "tag_key": tag_key,
                        "cost_type": cost_type,
                        "tag_values": map_vals,
                        "default_value": default_value,
                        "default_other_keys": default_other_keys,
                    }
                    mapping[metric_name] = existing_dict
                else:
                    mapping[metric_name] = {
                        cost_type: {
                            "tag_key": tag_key,
                            "cost_type": cost_type,
                            "tag_values": map_vals,
                            "default_value": default_value,
                            "default_other_keys": default_other_keys,
                        }
                    }

        return rates, mapping

    def setUp(self):
        """Set up a test with database objects."""
        super().setUp()
        # Use an OCP provider that does not have a cost model. Any OCP-on-X OCP provider will do
        self.provider_uuid = self.ocpaws_provider_uuid
        self.creator = ReportObjectCreator(self.schema)
        self.rates, self.mapping = self.build_tag_rates()
        self.cost_model = self.creator.create_cost_model(self.provider_uuid, Provider.PROVIDER_OCP, self.rates)

    def test_initializer(self):
        """Test initializer."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            self.assertEqual(cost_model_accessor.provider_uuid, self.provider_uuid)

    def test_infrastructure_tag_rates(self):
        """Test infrastructure rates property."""
        cost_type = "Infrastructure"

        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            result_infra_rates = cost_model_accessor.tag_infrastructure_rates
            for metric_name in result_infra_rates.keys():
                metric_rates = self.mapping.get(metric_name).get("Infrastructure")
                expected_key = metric_rates.get("tag_key")
                expected_vals = metric_rates.get("tag_values")
                expected_cost_type = metric_rates.get("cost_type")
                expected_dict = {expected_key: expected_vals}
                self.assertEqual(result_infra_rates.get(metric_name), expected_dict)
                self.assertEqual(expected_cost_type, cost_type)

    def test_supplementary_tag_rates(self):
        """Test supplementary rates property."""
        cost_type = "Supplementary"

        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            result_suppla_rates = cost_model_accessor.tag_supplementary_rates
            for metric_name in result_suppla_rates.keys():
                metric_rates = self.mapping.get(metric_name).get("Supplementary")
                expected_key = metric_rates.get("tag_key")
                expected_vals = metric_rates.get("tag_values")
                expected_cost_type = metric_rates.get("cost_type")
                expected_dict = {expected_key: expected_vals}
                self.assertEqual(result_suppla_rates.get(metric_name), expected_dict)
                self.assertEqual(expected_cost_type, cost_type)

    def test_default_infrastructure_rates(self):
        """Tests that the proper keys and values are added for the default rates"""
        cost_type = "Infrastructure"

        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            result_infra_rates = cost_model_accessor.tag_default_infrastructure_rates
            for metric_name in result_infra_rates.keys():
                metric_rates = self.mapping.get(metric_name).get("Infrastructure")
                expected_key = metric_rates.get("tag_key")
                expected_default_value = metric_rates.get("default_value")
                expected_default_keys = metric_rates.get("default_other_keys")
                expected_cost_type = metric_rates.get("cost_type")
                expected_dict = {
                    expected_key: {"default_value": expected_default_value, "defined_keys": expected_default_keys}
                }
                self.assertEqual(result_infra_rates.get(metric_name), expected_dict)
                self.assertEqual(expected_cost_type, cost_type)

    def test_default_supplementary_rates(self):
        """Tests that the proper keys and values are added for the default rates"""
        cost_type = "Supplementary"

        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            result_suppla_rates = cost_model_accessor.tag_default_supplementary_rates
            for metric_name in result_suppla_rates.keys():
                metric_rates = self.mapping.get(metric_name).get("Supplementary")
                expected_key = metric_rates.get("tag_key")
                expected_default_value = metric_rates.get("default_value")
                expected_default_keys = metric_rates.get("default_other_keys")
                expected_cost_type = metric_rates.get("cost_type")
                expected_dict = {
                    expected_key: {"default_value": expected_default_value, "defined_keys": expected_default_keys}
                }
                self.assertEqual(result_suppla_rates.get(metric_name), expected_dict)
                self.assertEqual(expected_cost_type, cost_type)

    def test_tag_rates_params_map(self):
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            test_params = cost_model_accessor.metric_to_tag_params_map
            for metric, metric_data in test_params.items():
                metric_data = test_params.get(metric)
                self.assertIsInstance(metric_data, list)
                self.assertEqual(len(metric_data), 2)  # one rate per cost type in mapping
                for tag_rate_param in metric_data:
                    rate_type = tag_rate_param.get("rate_type")
                    self.assertIsNotNone(rate_type)
                    expected_metadata = self.mapping.get(metric, {}).get(rate_type)
                    self.assertEqual(tag_rate_param.get("default_rate"), expected_metadata.get("default_value"))
                    self.assertEqual(tag_rate_param.get("tag_key"), expected_metadata.get("tag_key"))
                    tag_param_value_rates = tag_rate_param.get("value_rates")
                    self.assertIsNotNone(tag_param_value_rates)
                    for result_key, result_value in tag_param_value_rates.items():
                        self.assertEqual(result_value, expected_metadata.get("tag_values", {}).get(result_key))


class CostModelDBAccessorTagRatesPriceListTest(MasuTestCase):
    """Test Cases for the CostModelDBAccessor object with tag rates."""

    def setUp(self):
        """Set up a test with database objects."""
        super().setUp()
        # Use an OCP provider that does not have a cost model. Any OCP-on-X OCP provider will do
        self.provider_uuid = self.ocpaws_provider_uuid
        self.creator = ReportObjectCreator(self.schema)

        self.rates = [
            {
                "metric": {
                    "name": "node_cost_per_month",
                    "label_metric": "Node",
                    "label_measurement": "Count",
                    "label_measurement_unit": "node-month",
                },
                "description": "",
                "tag_rates": {
                    "tag_key": "app",
                    "tag_values": [
                        {
                            "unit": "USD",
                            "usage": {"unit": "USD", "usage_end": "2020-11-11", "usage_start": "2020-11-11"},
                            "value": 123,
                            "default": True,
                            "tag_value": "smoke",
                            "description": "",
                        }
                    ],
                },
                "cost_type": "Infrastructure",
            },
            {
                "metric": {
                    "name": "node_cost_per_month",
                    "label_metric": "Node",
                    "label_measurement": "Count",
                    "label_measurement_unit": "node-month",
                },
                "description": "",
                "tag_rates": {
                    "tag_key": "web",
                    "tag_values": [
                        {
                            "unit": "USD",
                            "usage": {"unit": "USD", "usage_end": "2020-11-11", "usage_start": "2020-11-11"},
                            "value": 456,
                            "default": True,
                            "tag_value": "smoker",
                            "description": "",
                        }
                    ],
                },
                "cost_type": "Infrastructure",
            },
        ]
        self.cost_model = self.creator.create_cost_model(self.provider_uuid, Provider.PROVIDER_OCP, self.rates)

    def test_initializer(self):
        """Test initializer."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            self.assertEqual(cost_model_accessor.provider_uuid, self.provider_uuid)

    def test_price_list_existing_metric_different_key(self):
        """
        Tests that the proper keys and values are added for the rates if
        different keys are used for the same metric
        """
        expected = {"app": {"smoke": 123}, "web": {"smoker": 456}}
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            result_infra_rates = cost_model_accessor.tag_infrastructure_rates.get("node_cost_per_month")
            self.assertEqual(result_infra_rates, expected)


class CostModelDBAccessorRatesByNameTest(MasuTestCase):
    """Test Cases for CostModelDBAccessor infrastructure_rates_by_name and supplementary_rates_by_name."""

    def setUp(self):
        """Set up a test with database objects."""
        super().setUp()
        self.provider_uuid = self.ocpaws_provider_uuid
        self.creator = ReportObjectCreator(self.schema)
        rates = [
            {
                "name": "CPU infrastructure",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
            {
                "name": "Memory infrastructure",
                "metric": {"name": "memory_gb_usage_per_hour"},
                "tiered_rates": [{"value": 0.03, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
            {
                "name": "Node supplementary",
                "metric": {"name": "node_cost_per_month"},
                "tiered_rates": [{"value": 100.0, "unit": "USD"}],
                "cost_type": "Supplementary",
            },
        ]
        self.cost_model = self.creator.create_cost_model(self.provider_uuid, Provider.PROVIDER_OCP, rates)

    def test_infrastructure_rates_by_name_returns_list(self):
        """Verify infrastructure_rates_by_name returns a list of dicts with metric, value, name."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            rates = cost_model_accessor.infrastructure_rates_by_name
        self.assertIsInstance(rates, list)
        self.assertEqual(len(rates), 2)
        for r in rates:
            self.assertIn("metric", r)
            self.assertIn("value", r)
            self.assertIn("name", r)

    def test_supplementary_rates_by_name_returns_list(self):
        """Verify supplementary_rates_by_name returns a list of dicts with metric, value, name."""
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            rates = cost_model_accessor.supplementary_rates_by_name
        self.assertIsInstance(rates, list)
        self.assertEqual(len(rates), 1)
        for r in rates:
            self.assertIn("metric", r)
            self.assertIn("value", r)
            self.assertIn("name", r)

    def test_rates_by_name_preserves_multiple_rates_for_same_metric(self):
        """Verify both infrastructure rates for cpu_core_usage_per_hour appear in infrastructure_rates_by_name."""
        from cost_models.models import CostModelMap

        rates = [
            {
                "name": "Base CPU rate",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.05, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
            {
                "name": "Premium CPU rate",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tiered_rates": [{"value": 0.10, "unit": "USD"}],
                "cost_type": "Infrastructure",
            },
        ]
        with schema_context(self.schema):
            CostModelMap.objects.filter(provider_uuid=self.provider_uuid).delete()
        self.creator.create_cost_model(self.provider_uuid, Provider.PROVIDER_OCP, rates)
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            by_name = cost_model_accessor.infrastructure_rates_by_name
        cpu_rates = [r for r in by_name if r["metric"] == "cpu_core_usage_per_hour"]
        self.assertEqual(len(cpu_rates), 2)
        names = {r["name"] for r in cpu_rates}
        self.assertEqual(names, {"Base CPU rate", "Premium CPU rate"})

    def test_tag_rate_names_property(self):
        """Verify tag_rate_names returns {metric: {tag_key: rate_name}}."""
        from cost_models.models import CostModelMap

        rates = [
            {
                "name": "JBoss tag rate",
                "metric": {"name": "cpu_core_usage_per_hour"},
                "tag_rates": {
                    "tag_key": "workload",
                    "tag_values": [{"tag_value": "jboss", "value": 40.0, "unit": "USD"}],
                },
                "cost_type": "Infrastructure",
            },
        ]
        with schema_context(self.schema):
            CostModelMap.objects.filter(provider_uuid=self.provider_uuid).delete()
        self.creator.create_cost_model(self.provider_uuid, Provider.PROVIDER_OCP, rates)
        with CostModelDBAccessor(self.schema, self.provider_uuid) as cost_model_accessor:
            names = cost_model_accessor.tag_rate_names
        self.assertEqual(names["cpu_core_usage_per_hour"]["workload"], "JBoss tag rate")


class CostModelRateNameThreadingTest(MasuTestCase):
    """Test that monthly cost SQL and tag usage SQL accept and use rate_name."""

    def setUp(self):
        """Set up a test with database objects."""
        super().setUp()
        self.provider_uuid = self.ocp_provider_uuid

    def test_populate_monthly_cost_sql_with_rate_name(self):
        """Verify populate_monthly_cost_sql with rate_name sets cost_model_rate_name on line items."""
        from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
        from reporting.models import OCPUsageLineItemDailySummary

        with OCPReportDBAccessor(schema=self.schema) as acc:
            acc.populate_monthly_cost_sql(
                cost_type="Node",
                rate_type="Infrastructure",
                rate=Decimal("100.00"),
                start_date=self.dh.this_month_start,
                end_date=self.dh.today,
                distribution="cpu",
                provider_uuid=self.provider_uuid,
                rate_name="Node monthly",
            )
        with schema_context(self.schema):
            rows = OCPUsageLineItemDailySummary.objects.filter(
                monthly_cost_type="Node",
                cost_model_rate_type="Infrastructure",
                usage_start__gte=self.dh.this_month_start,
            )
            self.assertTrue(
                rows.exists(),
                "populate_monthly_cost_sql should create Node monthly cost rows " "for the OCP-on-Prem cluster.",
            )
            for row in rows:
                self.assertEqual(
                    row.cost_model_rate_name,
                    "Node monthly",
                    f"Monthly cost row {row.uuid} should carry rate_name='Node monthly' "
                    f"but got '{row.cost_model_rate_name}'",
                )

    def test_populate_tag_usage_costs_with_tag_rate_names(self):
        """Verify populate_tag_usage_costs with tag_rate_names sets cost_model_rate_name on line items."""
        from masu.database.ocp_report_db_accessor import OCPReportDBAccessor
        from reporting.models import OCPUsageLineItemDailySummary

        # Use tag key "app" and value "banking" which are known to exist in the
        # test fixtures (seeded by ModelBakeryDataLoader on OCP-on-Prem cluster).
        infrastructure_rates = {"cpu_core_usage_per_hour": {"app": {"banking": Decimal("40")}}}
        supplementary_rates = {}
        tag_rate_names = {"cpu_core_usage_per_hour": {"app": "CPU app tag rate"}}

        # Clean up any previous Tag rows so we start from a known state
        with schema_context(self.schema):
            OCPUsageLineItemDailySummary.objects.filter(
                monthly_cost_type="Tag",
                cluster_id=self.ocp_cluster_id,
                usage_start__gte=self.dh.this_month_start,
            ).delete()

        with OCPReportDBAccessor(schema=self.schema) as acc:
            acc.populate_tag_usage_costs(
                infrastructure_rates,
                supplementary_rates,
                start_date=self.dh.this_month_start,
                end_date=self.dh.today,
                cluster_id=self.ocp_cluster_id,
                tag_rate_names=tag_rate_names,
            )
        with schema_context(self.schema):
            tag_rows = OCPUsageLineItemDailySummary.objects.filter(
                monthly_cost_type="Tag",
                usage_start__gte=self.dh.this_month_start,
                cluster_id=self.ocp_cluster_id,
            )
            self.assertTrue(
                tag_rows.exists(),
                "populate_tag_usage_costs should create Tag rows for the app:banking "
                "tag that exists in the test fixture data. If this fails, verify that "
                "the OCP-on-Prem cluster has rows with pod_labels containing "
                '{"app": "banking"}.',
            )
            for row in tag_rows:
                self.assertEqual(
                    row.cost_model_rate_name,
                    "CPU app tag rate",
                    f"Tag row {row.uuid} should carry rate_name='CPU app tag rate' "
                    f"but got '{row.cost_model_rate_name}'",
                )
