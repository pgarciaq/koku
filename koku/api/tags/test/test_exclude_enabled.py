#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
"""``exclude[enabled]`` across every tag handler (#900).

The four concrete exclude serializers (AWS, Azure, GCP, OCP — the rest
inherit) carried an ``enabled`` BooleanField cargo-culted from the filter
serializers when ``exclude[]`` support was built (#3869, 2022): the field
lists were mirrored one-for-one, but the query side only ever composed the
isnull exclusion plus ``category``. So ``exclude[enabled]`` validated, was
echoed back in the response's parameter block, and did nothing — on all
eight tag handlers, for four years. No consumer sends it (verified across
koku-ui and the IQE plugins), and on seven of the eight handlers the
underlying summary tables hold only enabled rows, so a "working"
implementation would mostly answer empty sets.

The field is no longer advertised (#900). The tags layer does not silently
drop a nested key it does not advertise: it answers 400 "Unsupported
parameter or invalid value" — the same treatment an invented exclude key
already gets. The plain ``filter[enabled]`` boolean is untouched and still
composes its predicate; the control test here guards that, so the removal
cannot be misread as touching the filter side.
"""
from unittest.mock import patch

from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory

from api.iam.test.iam_test_case import IamTestCase
from api.tags.all.openshift.view import OCPAllTagView
from api.tags.aws.openshift.view import OCPAWSTagView
from api.tags.aws.view import AWSTagView
from api.tags.azure.openshift.view import OCPAzureTagView
from api.tags.azure.view import AzureTagView
from api.tags.gcp.openshift.view import OCPGCPTagView
from api.tags.gcp.view import GCPTagView
from api.tags.ocp.view import OCPTagView

# One entry per tag view — the eight handlers that advertise ``exclude``.
PROVIDERS = (
    {"label": "aws", "view": AWSTagView},
    {"label": "azure", "view": AzureTagView},
    {"label": "gcp", "view": GCPTagView},
    {"label": "aws/openshift", "view": OCPAWSTagView},
    {"label": "azure/openshift", "view": OCPAzureTagView},
    {"label": "gcp/openshift", "view": OCPGCPTagView},
    {"label": "openshift", "view": OCPTagView},
    {"label": "all/openshift", "view": OCPAllTagView},
)

KEY = "tag900"
VALUE = "zq900"


def _url(exclude=None, exclude_value="false", filter_field=None, filter_value="false"):
    """Build a value-endpoint URL carrying exclude and/or filter fields.

    The value endpoint is used because it needs no summary-table fixtures: it
    composes its parameters and reads the value catalog.
    """
    query = f"filter[value]={VALUE}"
    if exclude is not None:
        query += f"&exclude[{exclude}]={exclude_value}"
    if filter_field is not None:
        query += f"&filter[{filter_field}]={filter_value}"
    return "/?" + query.replace("[", "%5B").replace("]", "%5D")


class ExcludeEnabledTest(IamTestCase):
    """``exclude[enabled]`` must be unsupported, not a validated no-op (#900)."""

    def _get(self, view_cls, url):
        """Run one tag view for one URL and return the response."""
        request = DRFRequest(APIRequestFactory().get(url))
        request.user = self.request_context["request"].user
        with patch.object(view_cls, "validate_key", return_value=True):
            return view_cls().get(request, key=KEY)

    def test_exclude_enabled_is_rejected_not_ignored(self):
        """Every tag view must answer 400 for ``exclude[enabled]``.

        Pre-fix the key validated and was echoed back with no effect on rows;
        the removal makes it the treatment an unadvertised key already gets.
        Both spellings are asserted so a ``default=True`` remnant cannot
        green a half-removal.
        """
        for cfg in PROVIDERS:
            for value in ("true", "false"):
                with self.subTest(provider=cfg["label"], value=value):
                    response = self._get(cfg["view"], _url(exclude="enabled", exclude_value=value))
                    self.assertEqual(
                        response.status_code,
                        400,
                        f"{cfg['label']}: exclude[enabled]={value} must be "
                        f"unsupported (#900), got {response.status_code}",
                    )

    def test_unadvertised_exclude_key_is_the_reference_treatment(self):
        """An invented exclude key already answers 400 — the reference behavior."""
        for cfg in PROVIDERS:
            with self.subTest(provider=cfg["label"]):
                response = self._get(cfg["view"], _url(exclude="notakey900"))
                self.assertEqual(
                    response.status_code,
                    400,
                    f"{cfg['label']}: an unadvertised exclude key must answer 400",
                )

    def test_plain_filter_enabled_still_supported(self):
        """The plain ``filter[enabled]`` boolean is untouched by the removal.

        This is the control: the fix removes the exclude-side echo only, and
        the filter boolean must keep composing its predicate everywhere it is
        advertised.
        """
        for cfg in PROVIDERS:
            with self.subTest(provider=cfg["label"]):
                response = self._get(cfg["view"], _url(filter_field="enabled", filter_value="false"))
                self.assertEqual(
                    response.status_code,
                    200,
                    f"{cfg['label']}: plain filter[enabled] must stay supported "
                    f"(#900 removes the exclude side only), got {response.status_code}",
                )
