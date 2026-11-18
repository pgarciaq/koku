#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""The plain ``enabled`` filter on the three OCP-on-cloud tag handlers (#895).

Each of these handlers composed ``('enabled__exact', True)`` whatever the
request asked for, so ``filter[enabled]=false`` returned the same rows as
``=true``. The summary tables behind them only hold keys whose flag is set for
the cloud provider -- the summary SQL inserts enabled keys and deletes the rest
-- so the only honest answer to ``false`` is the set ``true`` excludes, which
is empty, exactly as ``tags/aws/``, ``tags/azure/`` and ``tags/gcp/`` answer it.
"""
from urllib.parse import quote_plus
from urllib.parse import urlencode

from django_tenants.utils import tenant_context
from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory

from api.iam.test.iam_test_case import IamTestCase
from api.query_params import QueryParameters
from api.tags.aws.openshift.queries import OCPAWSTagQueryHandler
from api.tags.aws.openshift.view import OCPAWSTagView
from api.tags.azure.openshift.queries import OCPAzureTagQueryHandler
from api.tags.azure.openshift.view import OCPAzureTagView
from api.tags.gcp.openshift.queries import OCPGCPTagQueryHandler
from api.tags.gcp.openshift.view import OCPGCPTagView
from reporting.provider.aws.openshift.models import OCPAWSTagsSummary
from reporting.provider.azure.openshift.models import OCPAzureTagsSummary
from reporting.provider.gcp.openshift.models import OCPGCPTagsSummary

# Two months back: the tags summaries carry the two most recent months of
# fixture data, and the handler snaps the window to month start and end.
WINDOW = {
    "filter[resolution]": "monthly",
    "filter[time_scope_value]": "-2",
    "filter[time_scope_units]": "month",
}

CASES = (
    ("openshift/aws", OCPAWSTagView, OCPAWSTagQueryHandler, OCPAWSTagsSummary),
    ("openshift/azure", OCPAzureTagView, OCPAzureTagQueryHandler, OCPAzureTagsSummary),
    ("openshift/gcp", OCPGCPTagView, OCPGCPTagQueryHandler, OCPGCPTagsSummary),
)


class OCPOnCloudEnabledFilterTest(IamTestCase):
    """``filter[enabled]`` must bind the requested value on OCP-on-cloud tags (#895)."""

    def _keys(self, view_cls, enabled):
        """Return the tag keys the view answers for one ``enabled`` value."""
        params = dict(WINDOW, **{"filter[enabled]": enabled})
        request = DRFRequest(APIRequestFactory().get("/?" + urlencode(params, quote_via=quote_plus)))
        request.user = self.request_context["request"].user
        return {row.get("key") for row in view_cls().get(request).data.get("data")}

    def _window_keys(self, view_cls, handler_cls, model):
        """Return the table's keys for the window the handler itself derives.

        Read through the ORM with no ``enabled`` annotation, so the expectation
        does not come from the predicate under test.
        """
        request = self.factory.get("/?" + urlencode(WINDOW, quote_via=quote_plus))
        request.user = self.request_context["request"].user
        handler = handler_cls(QueryParameters(request, view_cls()))
        start = handler.dh.month_start(handler.start_datetime)
        end = handler.dh.month_end(handler.end_datetime)
        with tenant_context(self.tenant):
            return set(
                model.objects.filter(
                    cost_entry_bill__billing_period_start__gte=start,
                    cost_entry_bill__billing_period_start__lte=end,
                ).values_list("key", flat=True)
            )

    def test_true_returns_the_window_keys(self):
        """``enabled=true`` must keep returning every key the table holds.

        This is the guard for the fix below: a predicate made unsatisfiable
        would pass the ``false`` test and fail here instead.
        """
        for label, view_cls, handler_cls, model in CASES:
            with self.subTest(handler=label):
                expected = self._window_keys(view_cls, handler_cls, model)
                self.assertTrue(expected, msg=f"{label}: no fixture rows in the window to assert against")
                self.assertEqual(
                    expected,
                    self._keys(view_cls, "true"),
                    msg=f"{label}: filter[enabled]=true must still return every key the summary table holds",
                )

    def test_false_excludes_what_true_returns(self):
        """``enabled=false`` must exclude the enabled rows, not repeat them.

        These tables only hold keys whose flag is set for the cloud provider, so
        the complement is empty -- the same answer the cloud tag endpoints give,
        not the keys that are merely not registered for OCP.
        """
        for label, view_cls, _, _ in CASES:
            with self.subTest(handler=label):
                self.assertEqual(
                    set(),
                    self._keys(view_cls, "false"),
                    msg=(
                        f"{label}: filter[enabled]=false returned enabled keys; before the fix the map "
                        "bound ('enabled__exact', True) and the request got its opposite"
                    ),
                )
