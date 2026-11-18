#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""OCP-on-AWS Tag Query Handling."""
from copy import deepcopy

from django.db.models import Exists
from django.db.models import OuterRef

from api.models import Provider
from api.report.aws.openshift.provider_map import OCPAWSProviderMap
from api.tags.aws.queries import AWSTagQueryHandler
from api.tags.ocp.queries import OCPTagQueryHandler
from api.tags.queries import TagQueryHandler
from reporting.models import OCPAWSTagsSummary
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.aws.openshift.models import OCPAWSTagsValues


class OCPAWSTagQueryHandler(AWSTagQueryHandler, OCPTagQueryHandler):
    """Handles tag queries and responses for OCP-on-AWS."""

    provider = Provider.OCP_AWS
    # One source: the summary SQL inserts only keys whose flag is set for the
    # cloud provider and deletes rows for keys that lose it, so this annotation
    # is exactly what `filter[enabled]` binds. The OCP registry used to be a
    # second source here, but it answered "registered", not "enabled", and could
    # only add rows on `enabled=false`, where it meant "not registered for OCP"
    # (#895). `_get_tags` unions per-source results, so a second source also
    # makes `false` an OR of complements instead of the complement of `true`.
    data_sources = [
        {
            "db_table": OCPAWSTagsSummary,
            "db_column_period": "cost_entry_bill__billing_period",
            "annotations": {
                "enabled": Exists(
                    EnabledTagKeys.objects.filter(
                        provider_type=Provider.PROVIDER_AWS,
                        key=OuterRef("key"),
                        enabled=True,
                    )
                )
            },
        },
    ]
    TAGS_VALUES_SOURCE = [{"db_table": OCPAWSTagsValues, "fields": ["key"]}]
    SUPPORTED_FILTERS = AWSTagQueryHandler.SUPPORTED_FILTERS + OCPTagQueryHandler.SUPPORTED_FILTERS

    def __init__(self, parameters):
        """Establish AWS report query handler.

        Args:
            parameters    (QueryParameters): parameter object for query

        """
        self._parameters = parameters
        self._mapper = OCPAWSProviderMap(
            provider=self.provider,
            report_type=parameters.report_type,
            schema_name=parameters.tenant.schema_name,
            cost_type=parameters.cost_type,
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
                    "account": [
                        {"field": "account_aliases", "operation": "overlap", "composition_key": "account_filter"},
                        {"field": "usage_account_ids", "operation": "overlap", "composition_key": "account_filter"},
                    ],
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
                    "account": [
                        {
                            "field": "account_alias__account_alias",
                            "operation": "icontains",
                            "composition_key": "account_filter",
                        },
                        {"field": "usage_account_id", "operation": "icontains", "composition_key": "account_filter"},
                    ],
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
