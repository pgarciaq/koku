#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""OCP Query Handling for Reports."""
from django.db.models import Q
from django_tenants.utils import tenant_context

from api.models import Provider
from api.report.all.openshift.provider_map import OCPAllProviderMap
from api.report.aws.openshift.query_handler import OCPInfrastructureReportQueryHandlerBase
from api.report.ocp.provider_map import OCPProviderMap
from api.report.ocp.query_handler import OCPReportQueryHandler
from api.report.queries import is_grouped_by_project
from reporting.provider.aws.openshift.models import OCPAWSCostLineItemProjectDailySummaryP
from reporting.provider.azure.openshift.models import OCPAzureCostLineItemProjectDailySummaryP
from reporting.provider.gcp.openshift.models import OCPGCPCostLineItemProjectDailySummaryP


class OCPAllReportQueryHandler(OCPInfrastructureReportQueryHandlerBase):
    """Handles report queries and responses for OCP on All Infrastructure."""

    provider = Provider.OCP_ALL
    network_services = {
        "AmazonVPC",
        "AmazonCloudFront",
        "AmazonRoute53",
        "AmazonAPIGateway",
        "Virtual Network",
        "VPN",
        "DNS",
        "Traffic Manager",
        "ExpressRoute",
        "Load Balancer",
        "Application Gateway",
    }
    database_services = {
        "AmazonRDS",
        "AmazonDynamoDB",
        "AmazonElastiCache",
        "AmazonNeptune",
        "AmazonRedshift",
        "AmazonDocumentDB",
        "Database",
        "Cosmos DB",
        "Cache for Redis",
    }

    def __init__(self, parameters):
        """Establish OCP report query handler.
        Args:
            parameters    (QueryParameters): parameter object for query
        """
        self._mapper = OCPAllProviderMap(
            provider=self.provider, report_type=parameters.report_type, schema_name=parameters.tenant.schema_name
        )
        # Update which field is used to calculate cost by group by param.
        if is_grouped_by_project(parameters):
            self._report_type = parameters.report_type + "_by_project"
            self._mapper = OCPAllProviderMap(
                provider=self.provider, report_type=self._report_type, schema_name=parameters.tenant.schema_name
            )

        self.group_by_options = self._mapper.provider_map.get("group_by_options")
        self._limit = parameters.get_filter("limit")

        # super() needs to be called after _mapper and _limit is set
        super().__init__(parameters)


class OCPOnPremiseReportQueryHandler(OCPReportQueryHandler):
    """Handles report queries and responses for OCP on-premise (excluding cloud clusters).

    For on-premise clusters, we query OCPUsageLineItemDailySummary directly (like the regular
    OCP endpoint) and filter out clusters that have AWS, Azure, or GCP costs.
    """

    provider = Provider.OCP_ALL

    def __init__(self, parameters):
        """Establish OCP on-premise report query handler.

        Args:
            parameters    (QueryParameters): parameter object for query
        """
        # Use OCPProviderMap which queries OCPUsageLineItemDailySummary directly
        # and has proper cost model cost calculations
        mapper_class = OCPProviderMap
        self._limit = parameters.get_filter("limit")
        self._report_type = parameters.report_type
        # Update which field is used to calculate cost by group by param.
        if is_grouped_by_project(parameters) and parameters.report_type == "costs":
            self._report_type = parameters.report_type + "_by_project"
        self._mapper = mapper_class(
            provider=Provider.PROVIDER_OCP, report_type=self._report_type, schema_name=parameters.tenant.schema_name
        )
        self.group_by_options = self._mapper.report_type_map.get("group_by_options") or self._mapper.provider_map.get(
            "group_by_options"
        )
        if self._report_type == "gpu":
            self.group_by_alias = {"vendor": "vendor_name", "model": "model_name"}

        # Call parent __init__ which will set up query_filter and query_exclusions
        super(OCPReportQueryHandler, self).__init__(parameters)

    def _get_cloud_cluster_ids(self):
        """Get list of cluster IDs that are running on AWS, Azure, or GCP."""
        cloud_cluster_ids = set()
        with tenant_context(self.tenant):
            # Get AWS clusters
            aws_clusters = OCPAWSCostLineItemProjectDailySummaryP.objects.values_list(
                "cluster_id", flat=True
            ).distinct()
            cloud_cluster_ids.update(aws_clusters)

            # Get Azure clusters
            azure_clusters = OCPAzureCostLineItemProjectDailySummaryP.objects.values_list(
                "cluster_id", flat=True
            ).distinct()
            cloud_cluster_ids.update(azure_clusters)

            # Get GCP clusters
            gcp_clusters = OCPGCPCostLineItemProjectDailySummaryP.objects.values_list(
                "cluster_id", flat=True
            ).distinct()
            cloud_cluster_ids.update(gcp_clusters)

        return list(cloud_cluster_ids)

    def execute_query(self):
        """Execute query and exclude cloud clusters."""
        # Get cloud cluster IDs to exclude
        cloud_cluster_ids = self._get_cloud_cluster_ids()

        # Add exclusion for cloud clusters if there are any
        if cloud_cluster_ids:
            # Create Q object to exclude these cluster IDs
            cloud_cluster_exclusion = Q(cluster_id__in=cloud_cluster_ids)
            # Combine with existing exclusions if any
            if self.query_exclusions:
                self.query_exclusions = self.query_exclusions | cloud_cluster_exclusion
            else:
                self.query_exclusions = cloud_cluster_exclusion

        # Call parent execute_query which uses OCPUsageLineItemDailySummary
        # and already has proper cost model cost calculations
        return super().execute_query()
