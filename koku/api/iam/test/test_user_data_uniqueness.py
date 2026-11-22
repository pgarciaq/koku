#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
"""Uniqueness of Faker-drawn test usernames (#903).

``IamTestCase._create_user_data`` draws usernames from Faker's
``simple_profile``, whose effective pool is ~100k with heavy clustering
(169 duplicate draws per 5,000 measured) and whose mail is drawn
independently of the username. Inside one test class's atomic block the
setUpClass user plus every ``@RbacPermissions``-decorated test's user are
all created via ``get_or_create(username, email, customer)`` — so a
redrawn username with a different mail makes the ``get`` miss and the
``create`` violate ``api_user_username_key``, failing one random test
per full-suite run in a way that reads as a code regression.

The seed below reproduces the collision deterministically: pre-fix, the
duplicate ``hjohnson`` appears at draw 269.
"""
from unittest import mock

from django.test import SimpleTestCase
from faker import Faker

from api.iam.test.iam_test_case import IamTestCase


class UserDataUniquenessTest(SimpleTestCase):
    """Every drawn username must be unique — no birthday collisions (#903)."""

    def test_user_data_usernames_are_unique(self):
        """300 seeded draws must not repeat a username.

        Seed 1 is deterministic: pre-fix, ``hjohnson`` repeats at draw
        269 and the assertion fails there, naming the collision — the
        same mechanism that fails one random test per full-suite run.
        """
        fake = Faker()
        fake.seed_instance(1)
        with mock.patch.object(IamTestCase, "fake", fake):
            seen = set()
            for draw in range(300):
                username = IamTestCase._create_user_data()["username"]
                self.assertNotIn(
                    username,
                    seen,
                    f"draw {draw}: duplicate username {username!r} — the "
                    "birthday collision that fails one random test per "
                    "suite run (#903)",
                )
                seen.add(username)
