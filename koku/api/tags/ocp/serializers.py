#
# Copyright 2022 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
from rest_framework import serializers

from api.report.serializers import add_operator_specified_fields
from api.report.serializers import StringOrListField
from api.tags.serializers import ExcludeSerializer
from api.tags.serializers import FilterSerializer
from api.tags.serializers import TagsQueryParamSerializer


# `and:`/`or:`/`exact:` combine a set of values. `enabled` is a boolean
# literal, not a dimension: the operator has nothing to combine, and the
# handler binds the map entry's own parameter, so an operator-prefixed
# `enabled` reached QueryFilter(parameter=..., **{"parameter": ...}) and raised
# "multiple values for keyword argument 'parameter'" -- a 500 on a parameter
# this list advertised (#892). Not advertising it makes the tags layer reject
# the prefix as an unsupported nested key (400), the treatment `exact:node`
# already gets, instead of raising.
OCP_FILTER_OP_FIELDS = ["project", "cluster", "category"]


class OCPFilterSerializer(FilterSerializer):
    """Serializer for handling tag query parameter filter."""

    TYPE_CHOICES = (("pod", "pod"), ("storage", "storage"), ("*", "*"))
    type = serializers.ChoiceField(choices=TYPE_CHOICES, required=False)
    project = StringOrListField(child=serializers.CharField(), required=False)
    enabled = serializers.BooleanField(default=True, required=False)
    cluster = StringOrListField(child=serializers.CharField(), required=False)
    category = StringOrListField(child=serializers.CharField(), required=False)

    def __init__(self, *args, **kwargs):
        """Initialize the OCPFilterSerializer."""
        super().__init__(*args, **kwargs)
        add_operator_specified_fields(self.fields, OCP_FILTER_OP_FIELDS)


class OCPExcludeSerializer(ExcludeSerializer):
    """Serializer for handling tag query parameter filter."""

    TYPE_CHOICES = (("pod", "pod"), ("storage", "storage"), ("*", "*"))
    type = serializers.ChoiceField(choices=TYPE_CHOICES, required=False)
    project = StringOrListField(child=serializers.CharField(), required=False)
    enabled = serializers.BooleanField(default=True, required=False)
    cluster = StringOrListField(child=serializers.CharField(), required=False)
    category = StringOrListField(child=serializers.CharField(), required=False)

    def __init__(self, *args, **kwargs):
        """Initialize the OCPExcludeSerializer."""
        super().__init__(*args, **kwargs)
        add_operator_specified_fields(self.fields, OCP_FILTER_OP_FIELDS)


class OCPTagsQueryParamSerializer(TagsQueryParamSerializer):
    """Serializer for handling OCP tag query parameters."""

    EXCLUDE_SERIALIZER = OCPExcludeSerializer
    FILTER_SERIALIZER = OCPFilterSerializer

    exclude = OCPExcludeSerializer(required=False)
    filter = OCPFilterSerializer(required=False)
