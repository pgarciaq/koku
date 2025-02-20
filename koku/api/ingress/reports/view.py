#
# Copyright 2023 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""View for report posting."""
from uuid import UUID

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.common.pagination import ListPaginator
from api.ingress.reports.serializers import IngressReportsSerializer
from api.provider.models import Sources
from reporting.ingress.models import IngressReports


class IngressReportsDetailView(APIView):
    """
    View to fetch report details for specific source
    """

    permission_classes = [AllowAny]

    def get_object(self, source):
        """
        Helper method to get reports with given source
        """
        try:
            return IngressReports.objects.filter(source=source)
        except IngressReports.DoesNotExist:
            return None

    def get(self, request, *args, **kwargs):
        """
        Return reports for source.
        """
        source = kwargs["source"]
        try:
            UUID(source)
        except ValueError:
            return Response({"Error": "Invalid source uuid."}, status=status.HTTP_400_BAD_REQUEST)
        report_instance = self.get_object(source)
        if not report_instance:
            return Response({"Error": "Provider uuid not found."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = IngressReportsSerializer(report_instance, many=True)
        paginator = ListPaginator(serializer.data, request)
        return paginator.get_paginated_response(serializer.data)


class IngressReportsView(APIView):
    """
    View to interact with settings for a customer.
    """

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        """
        Return list of sources.
        """
        reports = IngressReports.objects.filter()
        serializer = IngressReportsSerializer(reports, many=True)
        paginator = ListPaginator(serializer.data, request)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        """Handle posted reports."""
        source_uuid = request.data.get("source")
        source_id = request.data.get("source")
        try:
            source = Sources.objects.filter(source_id=request.data.get("source")).first()
        except ValueError:
            try:
                source = Sources.objects.filter(koku_uuid=request.data.get("source")).first()
            except ValueError:
                pass
        if source:
            source_uuid = source.koku_uuid
            source_id = source.source_id
        data = {
            "source": source_uuid,
            "source_id": source_id,
            "reports_list": request.data.get("reports_list"),
            "bill_year": request.data.get("bill_year"),
            "bill_month": request.data.get("bill_month"),
        }
        serializer = IngressReportsSerializer(data=data)
        if serializer.is_valid(raise_exception=True):
            serializer.save()
            data["ingress_report_uuid"] = serializer.data.get("uuid")
            data["status"] = serializer.data.get("status")
            IngressReports.ingest(data)
            paginator = ListPaginator(data, request)
            return paginator.get_paginated_response(data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
