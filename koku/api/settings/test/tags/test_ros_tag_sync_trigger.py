#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Tests for ROS tag sync triggers in settings views."""
from unittest.mock import patch
from uuid import uuid4

import rest_framework.test
from django.urls import reverse
from django_tenants.utils import schema_context
from rest_framework import status

from api.iam.test.iam_test_case import IamTestCase
from api.provider.models import Provider
from reporting.provider.all.models import EnabledTagKeys
from reporting.provider.all.models import TagMapping


class RosTagSyncSettingsTriggerTest(IamTestCase):
    @patch("masu.processor.ros_tag_sync.schedule_ros_tag_sync")
    def test_enable_tag_triggers_sync(self, mock_schedule):
        with schema_context(self.schema_name):
            tag = EnabledTagKeys.objects.create(
                key=f"ros-sync-{uuid4().hex[:8]}",
                provider_type=Provider.PROVIDER_OCP,
                enabled=False,
            )

        url = reverse("tags-enable")
        client = rest_framework.test.APIClient()
        response = client.put(url, data={"ids": [str(tag.uuid)]}, format="json", **self.headers)

        self.assertEqual(status.HTTP_204_NO_CONTENT, response.status_code)
        mock_schedule.assert_called_once_with(self.schema_name)

    @patch("masu.processor.ros_tag_sync.schedule_ros_tag_sync")
    def test_tag_mapping_add_triggers_sync(self, mock_schedule):
        with schema_context(self.schema_name):
            parent = EnabledTagKeys.objects.create(
                key=f"parent-{uuid4().hex[:8]}",
                provider_type=Provider.PROVIDER_AWS,
                enabled=True,
            )
            child = EnabledTagKeys.objects.create(
                key=f"child-{uuid4().hex[:8]}",
                provider_type=Provider.PROVIDER_OCP,
                enabled=True,
            )

        url = reverse("tags-mapping-child-add")
        client = rest_framework.test.APIClient()
        response = client.put(
            url,
            data={"parent": str(parent.uuid), "children": [str(child.uuid)]},
            format="json",
            **self.headers,
        )

        self.assertEqual(status.HTTP_204_NO_CONTENT, response.status_code)
        mock_schedule.assert_called_once_with(self.schema_name)

        with schema_context(self.schema_name):
            TagMapping.objects.filter(parent=parent, child=child).delete()
