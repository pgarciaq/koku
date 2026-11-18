#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Value-endpoint array filters, across every handler that maps one (#871).

``reporting_*tags_values`` rows aggregate: one row for a key/value pair carries
every cluster, namespace, node or account that reported it. "The value is used
by entity X" therefore means "X is an element of the row's array", not
"the array's text rendering contains X" (``icontains``) and not "the whole
array is contained by {X}" (``contained_by``).

The seven cloud handlers used ``icontains`` against these arrays; OCP was moved
to ``overlap`` in #867 and is in the matrix too, because ``exact:`` is the same
defect wherever one entry maps an array (#886). These tests pin the same
semantics on the cloud maps and on the paths that consume the mapped operation:

* the user filter (``filter[project]=ns-a``),
* the RBAC access list (which is bound through the same map entry, #885),
* the wildcard (``filter[project]=*``), whose "match everything" predicate has
  no array form (#887),
* the ``exact:`` operator (``filter[exact:project]=ns-a``), which forces an
  exact lookup that compares whole arrays (#886).

They are table-driven rather than per-provider so the matrix is visible in one
place: every entry listed below is exercised on every axis.
"""
from collections import defaultdict

from django.contrib.postgres.fields import ArrayField
from django_tenants.utils import tenant_context

from api.iam.test.iam_test_case import IamTestCase
from api.tags.all.openshift.queries import OCPAllTagQueryHandler
from api.tags.all.openshift.view import OCPAllTagView
from api.tags.aws.openshift.queries import OCPAWSTagQueryHandler
from api.tags.aws.openshift.view import OCPAWSTagView
from api.tags.aws.queries import AWSTagQueryHandler
from api.tags.aws.view import AWSTagView
from api.tags.azure.openshift.queries import OCPAzureTagQueryHandler
from api.tags.azure.openshift.view import OCPAzureTagView
from api.tags.azure.queries import AzureTagQueryHandler
from api.tags.azure.view import AzureTagView
from api.tags.gcp.openshift.queries import OCPGCPTagQueryHandler
from api.tags.gcp.openshift.view import OCPGCPTagView
from api.tags.gcp.queries import GCPTagQueryHandler
from api.tags.gcp.view import GCPTagView
from api.tags.ocp.queries import OCPTagQueryHandler
from api.tags.ocp.view import OCPTagView
from reporting.provider.aws.models import AWSTagsValues
from reporting.provider.aws.openshift.models import OCPAWSTagsValues
from reporting.provider.azure.models import AzureTagsValues
from reporting.provider.azure.openshift.models import OCPAzureTagsValues
from reporting.provider.gcp.models import GCPTagsValues
from reporting.provider.gcp.openshift.models import OCPGCPTagsValues
from reporting.provider.ocp.models import OCPTagsValues

# The value filter is a substring match on a TextField, so one token selects
# this module's rows and nothing else. The two values differ only in a suffix
# that a substring match would blur: see ``LOOKALIKE``.
KEY = "tag871"
QUERY = "zq871"
SHARED = "zq871-shared"
LOOKALIKE = "zq871-lookalike"


def _member(filter_key, suffix):
    """Return an entity token for one filter map entry."""
    return f"{KEY}-{filter_key}-{suffix}"


# ``filters`` entries are asserted on the user-filter, RBAC, wildcard and
# ``exact:`` paths. Every entry is exercisable on every handler since #888: the
# OCP-on-all account filter used to name columns that exist only on the AWS
# catalog table, so it raised before it could match.
PROVIDERS = (
    {
        "label": "aws",
        "handler": AWSTagQueryHandler,
        "view": AWSTagView,
        "sources": (AWSTagsValues,),
        "filters": (
            {
                "key": "account",
                "fields": ("account_aliases", "usage_account_ids"),
                "access": "aws.account",
            },
        ),
    },
    {
        "label": "azure",
        "handler": AzureTagQueryHandler,
        "view": AzureTagView,
        "sources": (AzureTagsValues,),
        "filters": (
            {
                "key": "subscription_guid",
                "fields": ("subscription_guids",),
                "access": "azure.subscription_guid",
            },
        ),
    },
    {
        "label": "gcp",
        "handler": GCPTagQueryHandler,
        "view": GCPTagView,
        "sources": (GCPTagsValues,),
        "filters": (
            {"key": "account", "fields": ("account_ids",), "access": "gcp.account"},
            {
                "key": "gcp_project",
                "fields": ("project_ids", "project_names"),
                "access": "gcp.project",
            },
        ),
    },
    {
        "label": "aws/openshift",
        "handler": OCPAWSTagQueryHandler,
        "view": OCPAWSTagView,
        "sources": (OCPAWSTagsValues,),
        "filters": (
            {
                "key": "account",
                "fields": ("account_aliases", "usage_account_ids"),
                "access": "aws.account",
            },
            {"key": "project", "fields": ("namespaces",), "access": "openshift.project"},
            {
                "key": "cluster",
                "fields": ("cluster_ids", "cluster_aliases"),
                "access": "openshift.cluster",
            },
        ),
    },
    {
        "label": "azure/openshift",
        "handler": OCPAzureTagQueryHandler,
        "view": OCPAzureTagView,
        "sources": (OCPAzureTagsValues,),
        "filters": (
            {
                "key": "subscription_guid",
                "fields": ("subscription_guids",),
                "access": "azure.subscription_guid",
            },
            {"key": "project", "fields": ("namespaces",), "access": "openshift.project"},
            {
                "key": "cluster",
                "fields": ("cluster_ids", "cluster_aliases"),
                "access": "openshift.cluster",
            },
        ),
    },
    {
        "label": "gcp/openshift",
        "handler": OCPGCPTagQueryHandler,
        "view": OCPGCPTagView,
        "sources": (OCPGCPTagsValues,),
        "filters": (
            {"key": "account", "fields": ("account_ids",), "access": "gcp.account"},
            {"key": "project", "fields": ("namespaces",), "access": "openshift.project"},
            {
                "key": "cluster",
                "fields": ("cluster_ids", "cluster_aliases"),
                "access": "openshift.cluster",
            },
        ),
    },
    {
        "label": "openshift",
        "handler": OCPTagQueryHandler,
        "view": OCPTagView,
        "sources": (OCPTagsValues,),
        "filters": (
            {"key": "project", "fields": ("namespaces",), "access": "openshift.project"},
            {
                "key": "cluster",
                "fields": ("cluster_ids", "cluster_aliases"),
                "access": "openshift.cluster",
            },
        ),
    },
    {
        "label": "all/openshift",
        "handler": OCPAllTagQueryHandler,
        "view": OCPAllTagView,
        # Two catalog tables, so a match has to be found in both schemas.
        "sources": (OCPAzureTagsValues, OCPAWSTagsValues),
        "filters": (
            {
                "key": "account",
                # The Azure catalog keeps the cloud-account identifier in
                # `subscription_guids`; the handler aliases it to both account
                # columns (#888).
                "fields": ("account_aliases", "usage_account_ids", "subscription_guids"),
                "access": "aws.account",
            },
            {"key": "project", "fields": ("namespaces",), "access": "openshift.project"},
            {
                "key": "cluster",
                "fields": ("cluster_ids", "cluster_aliases"),
                "access": "openshift.cluster",
            },
        ),
    },
)


class TagValueArrayFilterTest(IamTestCase):
    """Element membership for every array-mapped value filter (#871)."""

    def setUp(self):
        """Seed, per catalog table, one shared row and one lookalike row."""
        super().setUp()
        with tenant_context(self.tenant):
            for model, entries in self._entries_by_model().items():
                self._seed(model, entries)

    def tearDown(self):
        """Remove every row this module seeded."""
        with tenant_context(self.tenant):
            for model in self._entries_by_model():
                model.objects.filter(key=KEY).delete()
        super().tearDown()

    @staticmethod
    def _entries_by_model():
        """Group every mapped entry by catalog table.

        Several handlers share a table: the OCP-on-all handler reads both the
        OCP-on-AWS and the OCP-on-Azure catalog, and each of those is also the
        sole source of its own handler. Seeding per handler would have the
        narrower map overwrite the wider one's tokens, so the rows are built
        once from the union of the entries that target each table.
        """
        by_model = {}
        for cfg in PROVIDERS:
            for model in cfg["sources"]:
                by_model.setdefault(model, []).extend(cfg["filters"])
        return by_model

    def _seed(self, model, entries):
        """Create the shared and lookalike rows for one catalog table.

        Every array field the model carries is populated, because the columns are
        non-null even when a handler does not map them (``project_ids`` on the
        OCP-on-GCP catalog is the example). Mapped fields get this module's
        tokens; unmapped ones get an empty array.

        A column can be addressed by more than one filter key: the OCP-on-all
        handler filters ``account`` on the same Azure column that the OCP-on-Azure
        handler filters as ``subscription_guid`` (#888). The row then has to hold
        a token for each of them, so the values are collected per key, not per
        column.
        """
        field_names = {field.name for field in model._meta.fields}
        mapped = defaultdict(set)
        for entry in entries:
            for field in entry["fields"]:
                if field in field_names:
                    mapped[field].add(entry["key"])

        blanks = {field.name: [] for field in model._meta.fields if isinstance(field, ArrayField)}
        shared = dict(blanks)
        for name, filter_keys in mapped.items():
            shared[name] = [
                token
                for filter_key in sorted(filter_keys)
                for token in (_member(filter_key, "a"), _member(filter_key, "b"))
            ]
        shared.update(key=KEY, value=SHARED)

        lookalike = dict(blanks)
        for name, filter_keys in mapped.items():
            # A superstring of each "a" member: a substring match on the array's
            # text form returns it, element membership must not.
            lookalike[name] = [_member(filter_key, "ab") for filter_key in sorted(filter_keys)]
        lookalike.update(key=KEY, value=LOOKALIKE)

        model.objects.filter(key=KEY).delete()
        model.objects.create(**shared)
        model.objects.create(**lookalike)

    def _values(self, cfg, extra_filter="", access=None):
        """Return the value endpoint's values for the shared key."""
        return self._fetch(cfg, f"filter[value]={QUERY}{extra_filter}", access)

    def _fetch(self, cfg, query, access=None, exact_value=None):
        """Return the value endpoint's values for one query string.

        ``exact_value`` sets ``filter[exact:value]`` on the parameters directly:
        the tags serializers do not register that field, so it cannot be sent in
        the query string, but the handler still resolves it.
        """
        url = f"/{KEY}/?{query}"
        query_params = self.mocked_query_params(url, cfg["view"], access=access)
        if exact_value is not None:
            query_params.set_filter(**{"exact:value": [exact_value]})
        query_params.kwargs = {"key": KEY}
        handler = cfg["handler"](query_params)
        data = handler.get_tag_values()
        if not data:
            return []
        return data[0].get("values", [])

    @staticmethod
    def _exact_arg(key, token):
        """Build an ``exact:`` filter argument; the colon is percent-encoded."""
        return f"filter%5Bexact%3A{key}%5D={token}"

    def test_shared_value_matches_each_of_its_members(self):
        """A value shared by two entities must match a filter naming either one."""
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                for member in ("a", "b"):
                    with self.subTest(provider=cfg["label"], filter=entry["key"], member=member):
                        token = _member(entry["key"], member)
                        self.assertEqual(
                            [SHARED],
                            self._values(cfg, f"&filter[{entry['key']}]={token}"),
                        )

    def test_lookalike_is_not_a_member(self):
        """A filter must match elements, not the array's text rendering."""
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                with self.subTest(provider=cfg["label"], filter=entry["key"]):
                    token = _member(entry["key"], "a")
                    self.assertEqual(
                        [SHARED],
                        self._values(cfg, f"&filter[{entry['key']}]={token}"),
                        msg=(
                            "a substring match returns the lookalike row as well; "
                            f"only the row holding {token} as an element may match"
                        ),
                    )

    def test_non_member_is_excluded(self):
        """Membership must not degrade into match-everything."""
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                with self.subTest(provider=cfg["label"], filter=entry["key"]):
                    token = _member(entry["key"], "unknown")
                    self.assertEqual([], self._values(cfg, f"&filter[{entry['key']}]={token}"))

    @staticmethod
    def _provider(label):
        """Return one provider configuration by label."""
        for cfg in PROVIDERS:
            if cfg["label"] == label:
                return cfg
        raise AssertionError(f"no provider named {label}")

    def test_ocp_all_reads_both_catalogs(self):
        """The OCP-on-all value endpoint queries both catalog tables (#888).

        A value that exists only in the Azure catalog must be returned, and the
        account filter must reach it: on that catalog the cloud-account
        identifier is the subscription guid, aliased to the account columns by
        the handler. Without the alias the account filter raises before the
        query runs, which is how the Azure catalog stayed unreachable.
        """
        cfg = self._provider("all/openshift")
        azure_only = f"{QUERY}-azure-only"
        token = _member("account", "a")
        with tenant_context(self.tenant):
            OCPAzureTagsValues.objects.create(
                key=KEY,
                value=azure_only,
                subscription_guids=[token],
                cluster_ids=[],
                cluster_aliases=[],
                namespaces=[_member("project", "a")],
                nodes=[],
            )

        self.assertIn(azure_only, self._values(cfg), msg="the Azure catalog must be queried")
        self.assertIn(
            azure_only,
            self._values(cfg, f"&filter[account]={token}"),
            msg="an account filter must match the Azure row through the subscription guid",
        )
        self.assertIn(
            azure_only,
            self._values(cfg, access={"aws.account": {"read": [token]}}),
            msg="the RBAC access list binds the same filter map entry",
        )

    def test_ocp_all_project_filters_the_key_endpoint(self):
        """``project`` must filter the key endpoint too, not be accepted and dropped (#888).

        The serializer on this handler registers ``project``, so a request for it
        answers 200. The key endpoint reads the tags summary tables, which need
        bill and period fixtures; what "ignored" looked like was a composed
        filter with no predicate for the namespace, so that is what is asserted
        here, for the user filter and for the RBAC grant.
        """
        cfg = self._provider("all/openshift")
        token = _member("project", "a")
        cases = (
            ("user filter", {"filter": {"project": [token]}}, None),
            ("rbac grant", {}, {"openshift.project": {"read": [token]}}),
        )
        for label, parameters, access in cases:
            with self.subTest(path=label):
                query_params = self.mocked_query_params(f"/?filter[key]={KEY}", cfg["view"], access=access)
                for key, value in parameters.get("filter", {}).items():
                    query_params.set_filter(**{key: value})
                handler = cfg["handler"](query_params)
                self.assertIn(
                    "namespace__",
                    str(handler.query_filter),
                    msg="without it the request is accepted and returns every namespace",
                )

    def test_rbac_access_list_uses_membership(self):
        """RBAC binds a list through the same map entry, so it must use the elements.

        The access list is not a string, and it is not a request to describe the
        whole row: a grant on one entity must keep a value another granted
        entity also reported (#885).
        """
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                with self.subTest(provider=cfg["label"], filter=entry["key"]):
                    access = {entry["access"]: {"read": [_member(entry["key"], "b")]}}
                    self.assertEqual([SHARED], self._values(cfg, access=access))

    def test_rbac_access_list_excludes_ungranted_values(self):
        """RBAC must still hide a value no granted entity reported."""
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                with self.subTest(provider=cfg["label"], filter=entry["key"]):
                    access = {entry["access"]: {"read": [_member(entry["key"], "unknown")]}}
                    self.assertEqual([], self._values(cfg, access=access))

    def test_wildcard_on_array_filter_adds_no_predicate(self):
        """``*`` has no array form: it must return every value, not raise (#887)."""
        expected = sorted([SHARED, LOOKALIKE])
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                with self.subTest(provider=cfg["label"], filter=entry["key"]):
                    self.assertEqual(
                        expected,
                        sorted(self._values(cfg, f"&filter[{entry['key']}]=*")),
                    )

    def test_wildcard_response_matches_the_unfiltered_response(self):
        """A wildcard filter removes no values, so the body must not change."""
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                with self.subTest(provider=cfg["label"], filter=entry["key"]):
                    self.assertEqual(
                        sorted(self._values(cfg)),
                        sorted(self._values(cfg, f"&filter[{entry['key']}]=*")),
                    )

    def test_exact_filter_matches_membership(self):
        """``exact:`` on an array filter must mean "this element is present" (#886).

        An exact lookup on an array column compares the whole array against the
        request, so a value that two entities reported can never match it. An
        element is present or it is not: there is no looser comparison for
        ``exact`` to tighten, so the entry keeps its membership lookup.
        """
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                for member in ("a", "b"):
                    with self.subTest(provider=cfg["label"], filter=entry["key"], member=member):
                        token = _member(entry["key"], member)
                        self.assertEqual(
                            [SHARED],
                            self._values(cfg, f"&{self._exact_arg(entry['key'], token)}"),
                        )

    def test_exact_filter_response_matches_the_plain_filter_response(self):
        """``exact:`` must not change which values an array filter answers.

        The plain filter already compares elements, so the two spellings answer
        the same rows, for a member and for a token nothing holds.
        """
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                for token in (_member(entry["key"], "a"), _member(entry["key"], "unknown")):
                    with self.subTest(provider=cfg["label"], filter=entry["key"], token=token):
                        self.assertEqual(
                            self._values(cfg, f"&filter[{entry['key']}]={token}"),
                            self._values(cfg, f"&{self._exact_arg(entry['key'], token)}"),
                        )

    def test_exact_wildcard_on_array_filter_adds_no_predicate(self):
        """``exact:`` on a wildcard follows the wildcard rule, it does not match nothing.

        ``*`` has no array form, so the filter adds no predicate; an ``exact``
        lookup bound to ``*`` would instead compare the whole array to it and
        drop every value (#887, #886).
        """
        expected = sorted([SHARED, LOOKALIKE])
        for cfg in PROVIDERS:
            for entry in cfg["filters"]:
                with self.subTest(provider=cfg["label"], filter=entry["key"]):
                    self.assertEqual(
                        expected,
                        sorted(self._values(cfg, f"&{self._exact_arg(entry['key'], '*')}")),
                    )

    def test_exact_filter_on_text_column_stays_exact(self):
        """The array rule must not loosen ``exact:`` on a text column.

        The tags serializers do not register ``exact:value`` today, so the
        argument is set on the parameters directly: this pins the operator the
        handler picks, and would catch a future registration that silently
        turned exact text matching into a substring match.
        """
        prefix = QUERY[:-1]
        for cfg in PROVIDERS:
            with self.subTest(provider=cfg["label"]):
                self.assertEqual(
                    sorted([SHARED, LOOKALIKE]),
                    sorted(self._fetch(cfg, f"filter[value]={prefix}")),
                    msg="control: the prefix is a substring of both seeded values",
                )
                self.assertEqual(
                    [SHARED],
                    self._fetch(cfg, f"filter[value]={prefix}", exact_value=SHARED),
                    msg="an exact value must still be matched exactly",
                )
                self.assertEqual(
                    [],
                    self._fetch(cfg, f"filter[value]={prefix}", exact_value=prefix),
                    msg="exact must not degrade into the substring match",
                )
