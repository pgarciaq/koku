#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Operator prefixes on the ``enabled`` boolean, across every tag handler (#892).

The tags serializers advertise ``and:``/``or:``/``exact:`` for a fixed list of
filter keys because those operators combine a set of values. ``enabled`` is a
boolean literal, not a set of entities, yet the OCP dimension list advertised
the prefixes for it, and the four OCP-family handlers had no literal to combine:

* they bind the filter map entry's own ``parameter`` (derived from the plain
  ``enabled`` key), and the operator path passes ``parameter=`` too, so the
  prefixed key reached ``QueryFilter(parameter=..., **{"parameter": ...})`` and
  raised ``TypeError: got multiple values for keyword argument 'parameter'``;
* the response was a 500 for a parameter the endpoint itself advertised.

``enabled`` is no longer advertised with an operator prefix (#892). The tags
layer does not silently drop a nested key it does not advertise: it answers 400
"Unsupported parameter or invalid value". So the fix makes the prefix unsupported
everywhere, which is the treatment ``exact:node`` already gets on these handlers,
instead of a 500 on four of them and an ignored parameter on the others.

The plain ``filter[enabled]`` boolean is untouched and still composes its
predicate; the control test here guards that, so the fix cannot be misread as
removing the boolean, and the operator prefixes on the real dimensions must keep
answering 200.
"""
from unittest.mock import patch

from rest_framework.request import Request as DRFRequest
from rest_framework.test import APIRequestFactory

from api.iam.test.iam_test_case import IamTestCase
from api.query_params import QueryParameters
from api.tags.all.openshift.queries import OCPAllTagQueryHandler
from api.tags.all.openshift.serializers import OCPAllExcludeSerializer
from api.tags.all.openshift.serializers import OCPAllFilterSerializer
from api.tags.all.openshift.view import OCPAllTagView
from api.tags.aws.openshift.queries import OCPAWSTagQueryHandler
from api.tags.aws.openshift.serializers import OCPAWSExcludeSerializer
from api.tags.aws.openshift.serializers import OCPAWSFilterSerializer
from api.tags.aws.openshift.view import OCPAWSTagView
from api.tags.aws.queries import AWSTagQueryHandler
from api.tags.aws.serializers import AWSExcludeSerializer
from api.tags.aws.serializers import AWSFilterSerializer
from api.tags.aws.view import AWSTagView
from api.tags.azure.openshift.queries import OCPAzureTagQueryHandler
from api.tags.azure.openshift.serializers import OCPAzureExcludeSerializer
from api.tags.azure.openshift.serializers import OCPAzureFilterSerializer
from api.tags.azure.openshift.view import OCPAzureTagView
from api.tags.azure.queries import AzureTagQueryHandler
from api.tags.azure.serializers import AzureExcludeSerializer
from api.tags.azure.serializers import AzureFilterSerializer
from api.tags.azure.view import AzureTagView
from api.tags.gcp.openshift.queries import OCPGCPTagQueryHandler
from api.tags.gcp.openshift.serializers import OCPGCPExcludeSerializer
from api.tags.gcp.openshift.serializers import OCPGCPFilterSerializer
from api.tags.gcp.openshift.view import OCPGCPTagView
from api.tags.gcp.queries import GCPTagQueryHandler
from api.tags.gcp.serializers import GCPExcludeSerializer
from api.tags.gcp.serializers import GCPFilterSerializer
from api.tags.gcp.view import GCPTagView
from api.tags.ocp.queries import OCPTagQueryHandler
from api.tags.ocp.serializers import OCPExcludeSerializer
from api.tags.ocp.serializers import OCPFilterSerializer
from api.tags.ocp.view import OCPTagView

# The prefixes `add_operator_specified_fields` registers.
OPERATORS = ("and", "or", "exact")

# One entry per tag view. `supports_enabled` marks the handlers whose query
# handler consumes the plain `enabled` boolean; the OCP-on-all handler
# inherits the serializer field but has no such entry, which is a separate
# question from this module and is not asserted here.
#
# `binds_enabled_value` marks the handlers whose map entry derives the value
# from the request. Before #895 the three OCP-on-cloud maps hard-coded
# `"parameter": True` in both branches; they now bind the requested value like
# the rest of the family, so the assertion applies to every entry listed here.
PROVIDERS = (
    {
        "label": "aws",
        "view": AWSTagView,
        "handler": AWSTagQueryHandler,
        "supports_enabled": True,
        "binds_enabled_value": True,
    },
    {
        "label": "azure",
        "view": AzureTagView,
        "handler": AzureTagQueryHandler,
        "supports_enabled": True,
        "binds_enabled_value": True,
    },
    {
        "label": "gcp",
        "view": GCPTagView,
        "handler": GCPTagQueryHandler,
        "supports_enabled": True,
        "binds_enabled_value": True,
    },
    {
        "label": "aws/openshift",
        "view": OCPAWSTagView,
        "handler": OCPAWSTagQueryHandler,
        "supports_enabled": True,
        "binds_enabled_value": True,
    },
    {
        "label": "azure/openshift",
        "view": OCPAzureTagView,
        "handler": OCPAzureTagQueryHandler,
        "supports_enabled": True,
        "binds_enabled_value": True,
    },
    {
        "label": "gcp/openshift",
        "view": OCPGCPTagView,
        "handler": OCPGCPTagQueryHandler,
        "supports_enabled": True,
        "binds_enabled_value": True,
    },
    {
        "label": "openshift",
        "view": OCPTagView,
        "handler": OCPTagQueryHandler,
        "supports_enabled": True,
        "binds_enabled_value": True,
    },
    {
        "label": "all/openshift",
        "view": OCPAllTagView,
        "handler": OCPAllTagQueryHandler,
        "supports_enabled": False,
        "binds_enabled_value": False,
    },
)

OCP_DIMENSIONS = ("project", "cluster", "category")

# Every serializer built on the OCP dimension list -- five filter serializers and
# the five exclude serializers that share the constant -- plus the cloud ones.
# `keeps` lists the keys that must still carry the operator prefixes, so a fix
# that removed the whole mechanism fails here. `enabled` is the only key the
# operator lists lose.
SERIALIZERS = (
    ("ocp filter", OCPFilterSerializer, OCP_DIMENSIONS),
    ("ocp exclude", OCPExcludeSerializer, OCP_DIMENSIONS),
    ("aws/openshift filter", OCPAWSFilterSerializer, OCP_DIMENSIONS + ("account",)),
    ("aws/openshift exclude", OCPAWSExcludeSerializer, OCP_DIMENSIONS + ("account",)),
    ("azure/openshift filter", OCPAzureFilterSerializer, OCP_DIMENSIONS + ("subscription_guid",)),
    ("azure/openshift exclude", OCPAzureExcludeSerializer, OCP_DIMENSIONS + ("subscription_guid",)),
    ("gcp/openshift filter", OCPGCPFilterSerializer, OCP_DIMENSIONS + ("account", "gcp_project")),
    ("gcp/openshift exclude", OCPGCPExcludeSerializer, OCP_DIMENSIONS + ("account", "gcp_project")),
    ("all/openshift filter", OCPAllFilterSerializer, OCP_DIMENSIONS + ("account", "subscription_guid")),
    ("all/openshift exclude", OCPAllExcludeSerializer, OCP_DIMENSIONS + ("account", "subscription_guid")),
    ("aws filter", AWSFilterSerializer, ("account", "org_unit_id")),
    ("aws exclude", AWSExcludeSerializer, ("account", "org_unit_id")),
    ("azure filter", AzureFilterSerializer, ("subscription_guid",)),
    ("azure exclude", AzureExcludeSerializer, ("subscription_guid",)),
    ("gcp filter", GCPFilterSerializer, ("account", "gcp_project")),
    ("gcp exclude", GCPExcludeSerializer, ("account", "gcp_project")),
)

KEY = "tag892"
VALUE = "zq892"


def _url(field, value="false"):
    """Build a value-endpoint URL carrying one filter field.

    The value endpoint is used because it needs no summary-table fixtures: it
    composes its filter from the parameters and reads the value catalog.
    """
    query = f"filter[value]={VALUE}&filter[{field}]={value}"
    return "/?" + query.replace("[", "%5B").replace("]", "%5D").replace(":", "%3A")


class OperatorPrefixedEnabledFilterTest(IamTestCase):
    """An operator prefix on ``enabled`` must be unsupported, never a 500 (#892)."""

    def _get(self, view_cls, field, key=KEY):
        """Run one tag view for one filter field and return the response."""
        request = DRFRequest(APIRequestFactory().get(_url(field)))
        request.user = self.request_context["request"].user
        with patch.object(view_cls, "validate_key", return_value=True):
            return view_cls().get(request, key=key)

    def test_operator_prefixed_enabled_is_rejected_not_raised(self):
        """Every tag view must answer 400 for the prefixes it used to advertise.

        Pre-fix the four OCP-family handlers raised while composing the filter,
        so the failure happened before any query ran and the status code is the
        whole observation; the other four accepted the key and ignored it.
        """
        for cfg in PROVIDERS:
            for operator in OPERATORS:
                field = f"{operator}:enabled"
                with self.subTest(provider=cfg["label"], field=field):
                    response = self._get(cfg["view"], field)
                    self.assertEqual(
                        400,
                        response.status_code,
                        msg=(
                            f"filter[{field}] must be rejected as an unsupported nested "
                            f"key; got {response.status_code}: {getattr(response, 'data', None)}"
                        ),
                    )

    def test_operator_prefixed_enabled_matches_an_unadvertised_dimension(self):
        """``enabled`` must get the treatment an unadvertised key already gets.

        ``node`` is a real filter dimension that these serializers never
        advertise with an operator prefix, so it is the reference behaviour:
        the same status for both keys means the fix removed an inconsistency
        rather than inventing a rule for the boolean.
        """
        for cfg in PROVIDERS:
            with self.subTest(provider=cfg["label"]):
                reference = self._get(cfg["view"], "exact:node", key=KEY)
                response = self._get(cfg["view"], "exact:enabled")
                self.assertEqual(
                    reference.status_code,
                    response.status_code,
                    msg="enabled and node are both unadvertised dimensions and must be treated alike",
                )

    def test_operator_prefixed_enabled_is_not_advertised(self):
        """No tags serializer may advertise an operator prefix for the boolean."""
        for label, serializer_cls, _ in SERIALIZERS:
            fields = serializer_cls().fields
            for operator in OPERATORS:
                field = f"{operator}:enabled"
                with self.subTest(serializer=label, field=field):
                    self.assertNotIn(
                        field,
                        fields,
                        msg=(
                            f"{label} advertises {field}; the handler binds the map "
                            "entry's own parameter, so composing a value into it raises (#892)"
                        ),
                    )

    def test_the_boolean_filter_field_is_still_advertised(self):
        """The fix removes the operator prefixes, not ``enabled`` itself."""
        for label, serializer_cls, _ in SERIALIZERS:
            with self.subTest(serializer=label):
                self.assertIn(
                    "enabled",
                    serializer_cls().fields,
                    msg=f"{label} must keep the plain boolean filter field",
                )

    def test_operator_prefixes_are_still_advertised_on_the_dimensions(self):
        """Every dimension keeps its prefixes: only the boolean lost them."""
        for label, serializer_cls, keeps in SERIALIZERS:
            fields = serializer_cls().fields
            for dimension in keeps:
                for operator in OPERATORS:
                    field = f"{operator}:{dimension}"
                    with self.subTest(serializer=label, field=field):
                        self.assertIn(
                            field,
                            fields,
                            msg=f"{label} must still advertise the operator prefix for {dimension}",
                        )

    def test_advertised_operator_prefix_still_answers(self):
        """An advertised prefix must keep working end to end.

        ``exact:project`` is the closest neighbour of the removed key, so a fix
        that broke the operator path itself -- rather than one entry in the
        advertised list -- fails here.
        """
        for cfg in PROVIDERS:
            with self.subTest(provider=cfg["label"]):
                response = self._get(cfg["view"], "exact:project", key=KEY)
                self.assertEqual(
                    200,
                    response.status_code,
                    msg=f"exact:project must still be accepted: {getattr(response, 'data', None)}",
                )

    def test_plain_enabled_still_composes_a_predicate(self):
        """``filter[enabled]`` is the supported spelling and must keep working.

        This is the control for the fix: the boolean leaves the *operator* lists
        only, so the plain key must still reach the filter map and compose a
        predicate on every handler that consumes it. Where the map binds the
        requested value, the composed predicate must carry it too.
        """
        for cfg in PROVIDERS:
            if not cfg["supports_enabled"]:
                continue
            with self.subTest(provider=cfg["label"]):
                request = self.factory.get("/?filter%5Benabled%5D=false")
                request.user = self.request_context["request"].user
                parameters = QueryParameters(request, cfg["view"]())
                handler = cfg["handler"](parameters)
                self.assertEqual(
                    False,
                    parameters.get_filter("enabled"),
                    msg="the plain boolean must stay in the parameters",
                )
                composed = str(handler.query_filter)
                self.assertIn(
                    "enabled__exact",
                    composed,
                    msg=f"the plain boolean must still compose a predicate: {composed}",
                )
                if cfg["binds_enabled_value"]:
                    self.assertIn(
                        "('enabled__exact', False)",
                        composed,
                        msg=f"the requested value must be bound, not a default: {composed}",
                    )
