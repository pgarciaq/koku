#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""OCP-on-Azure Tag Query Handling."""
from copy import deepcopy

from django.db.models import Exists
from django.db.models import OuterRef

from api.models import Provider
from api.report.azure.openshift.provider_map import OCPAzureProviderMap
from api.tags.azure.queries import AzureTagQueryHandler
from api.tags.ocp.queries import OCPTagQueryHandler
from api.tags.queries import TagQueryHandler
from reporting.models import OCPAzureTagsSummary
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.azure.openshift.models import OCPAzureTagsValues


class OCPAzureTagQueryHandler(AzureTagQueryHandler, OCPTagQueryHandler):
    """Handles tag queries and responses for OCP-on-Azure."""

    provider = Provider.OCP_AZURE
    # One source: the summary SQL inserts only keys whose flag is set for the
    # cloud provider and deletes rows for keys that lose it, so this annotation
    # is exactly what `filter[enabled]` binds. The OCP registry used to be a
    # second source here, but it answered "registered", not "enabled", and could
    # only add rows on `enabled=false`, where it meant "not registered for OCP"
    # (#895). `_get_tags` unions per-source results, so a second source also
    # makes `false` an OR of complements instead of the complement of `true`.
    data_sources = [
        {
            "db_table": OCPAzureTagsSummary,
            "db_column_period": "cost_entry_bill__billing_period",
            "annotations": {
                "enabled": Exists(
                    EnabledTagKeys.objects.filter(provider_type=Provider.PROVIDER_AZURE)
                    .filter(key=OuterRef("key"))
                    .filter(enabled=True)
                )
            },
        },
    ]
    TAGS_VALUES_SOURCE = [{"db_table": OCPAzureTagsValues, "fields": ["key"]}]
    SUPPORTED_FILTERS = AzureTagQueryHandler.SUPPORTED_FILTERS + OCPTagQueryHandler.SUPPORTED_FILTERS

    def __init__(self, parameters):
        """Establish Azure report query handler.

        Args:
            parameters    (QueryParameters): parameter object for query

        """
        self._parameters = parameters
        self._mapper = OCPAzureProviderMap(
            provider=self.provider, report_type=parameters.report_type, schema_name=parameters.tenant.schema_name
        )
        if parameters.get_filter("enabled") is None:
            parameters.set_filter(**{"enabled": True})
        # super() needs to be called after _mapper is set
        super().__init__(parameters)

    @property
    def filter_map(self):
        """Establish which filter map to use based on tag API."""
        filter_map = deepcopy(TagQueryHandler.FILTER_MAP)
        enabled_parameter = self._parameters.get_filter("enabled") in (None, True)
        if self._parameters.get_filter("value"):
            filter_map.update(
                {
                    "subscription_guid": {"field": "subscription_guids", "operation": "overlap"},
                    "project": {"field": "namespaces", "operation": "overlap"},
                    "cluster": [
                        {"field": "cluster_ids", "operation": "overlap", "composition_key": "cluster_filter"},
                        {"field": "cluster_aliases", "operation": "overlap", "composition_key": "cluster_filter"},
                    ],
                    "enabled": {"field": "enabled", "operation": "exact", "parameter": enabled_parameter},
                }
            )
        else:
            filter_map.update(
                {
                    "subscription_guid": {"field": "subscription_guid", "operation": "icontains"},
                    "project": {"field": "namespace", "operation": "icontains"},
                    "cluster": [
                        {
                            "field": "report_period__cluster_id",
                            "operation": "icontains",
                            "composition_key": "cluster_filter",
                        },
                        {
                            "field": "report_period__cluster_alias",
                            "operation": "icontains",
                            "composition_key": "cluster_filter",
                        },
                    ],
                    "enabled": {"field": "enabled", "operation": "exact", "parameter": enabled_parameter},
                }
            )
        return filter_map
