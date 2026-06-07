#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Test the IAM serializers."""
import uuid

from rest_framework.exceptions import ValidationError

from .iam_test_case import IamTestCase
from api.iam.serializers import AdminCustomerSerializer
from api.iam.serializers import create_schema_name
from api.iam.serializers import CustomerSerializer
from api.iam.serializers import normalize_org_id
from api.iam.serializers import UserSerializer


class NormalizeOrgIdTest(IamTestCase):
    """Tests for org_id normalization."""

    def test_bare_org_id_unchanged(self):
        """Bare numeric org_id must not be modified."""
        self.assertEqual("1234567", normalize_org_id("1234567"))

    def test_strips_org_prefix(self):
        """org-prefixed org_id must be stripped."""
        self.assertEqual("1234567", normalize_org_id("org1234567"))

    def test_org_prefix_non_numeric_suffix_unchanged(self):
        """org_id starting with org but not followed by digits must be left alone."""
        self.assertEqual("organization", normalize_org_id("organization"))

    def test_create_schema_name_idempotent(self):
        """create_schema_name must produce the same schema for bare and prefixed org_id."""
        self.assertEqual("org1234567", create_schema_name("1234567"))
        self.assertEqual("org1234567", create_schema_name("org1234567"))


class CustomerSerializerTest(IamTestCase):
    """Tests for the customer serializer."""

    def test_create_customer(self):
        """Test creating a customer."""
        # create the customers
        customer = self.create_mock_customer_data()
        instance = None
        serializer = CustomerSerializer(data=customer)
        if serializer.is_valid(raise_exception=True):
            instance = serializer.save()

        schema_name = serializer.data.get("schema_name")

        self.assertIsNone(schema_name)
        self.assertFalse("schema_name" in serializer.data)
        self.assertEqual(customer["account_id"], instance.account_id)
        self.assertIsInstance(instance.uuid, uuid.UUID)


class AdminCustomerSerializerTest(IamTestCase):
    """Tests for the admin customer serializer."""

    def test_schema_name_present(self):
        """Test that the serializer contains schema_name."""
        customer = self.create_mock_customer_data()
        serializer = AdminCustomerSerializer(data=customer)
        serializer.is_valid()
        serializer.save()
        expected_schema_name = create_schema_name(serializer.data.get("org_id"))
        schema_name = serializer.data.get("schema_name")
        self.assertIsNotNone(schema_name)
        self.assertEqual(schema_name, expected_schema_name)


class UserSerializerTest(IamTestCase):
    """Tests for the user serializer."""

    def setUp(self):
        """Create test case objects."""
        self.user_data = [self._create_user_data(), self._create_user_data()]

    def test_create_user(self):
        """Test creating a user."""
        # create the users
        for user in self.user_data:
            instance = None
            serializer = UserSerializer(data=user)
            if serializer.is_valid(raise_exception=True):
                instance = serializer.save()

            self.assertEqual(user["username"], instance.username)

    def test_uuid_field(self):
        """Test that we generate a uuid."""
        # create the user
        instance = None
        serializer = UserSerializer(data=self.user_data[0])
        if serializer.is_valid(raise_exception=True):
            instance = serializer.save()

        self.assertIsInstance(instance.uuid, uuid.UUID)

    def test_unique_user(self):
        """Test that a user must be unique."""
        # create the user
        serializer_1 = UserSerializer(data=self.user_data[0])
        if serializer_1.is_valid(raise_exception=True):
            serializer_1.save()

        serializer_2 = UserSerializer(data=self.user_data[0])
        with self.assertRaises(ValidationError):
            if serializer_2.is_valid(raise_exception=True):
                serializer_2.save()
