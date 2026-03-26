#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
from django.test import TestCase

from api.settings.ros_custom_timeframes import ROSCustomTimeframesSerializer


class TestROSCustomTimeframesSerializer(TestCase):
    """Tests for ROS custom timeframes validation (Koku is the single source of truth)."""

    def test_valid_three_terms(self):
        """A valid 3-term config with business hours disabled passes validation."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 3},
                {"name": "term2", "duration_days": 20},
                {"name": "term3", "duration_days": 60},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_valid_single_term(self):
        """Only term1 is required; term2 and term3 are optional."""
        data = {
            "terms": [{"name": "term1", "duration_days": 5}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_valid_two_terms(self):
        """Two properly ordered terms passes validation."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 5},
                {"name": "term2", "duration_days": 30},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_reject_term2_without_term1(self):
        """Cannot define term2 without term1 (sequential rule)."""
        data = {
            "terms": [{"name": "term2", "duration_days": 10}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_term3_without_term2(self):
        """Cannot define term3 without term2."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 1},
                {"name": "term3", "duration_days": 30},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_terms_not_ascending(self):
        """Terms must be ordered shorter to longer."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 30},
                {"name": "term2", "duration_days": 10},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_duplicate_durations(self):
        """All term durations must be distinct."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 7},
                {"name": "term2", "duration_days": 7},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_duration_below_minimum(self):
        """Duration must be at least 1 day."""
        data = {
            "terms": [{"name": "term1", "duration_days": 0}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_negative_duration(self):
        """Negative durations are rejected."""
        data = {
            "terms": [{"name": "term1", "duration_days": -5}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_duration_above_maximum(self):
        """Duration must not exceed 90 days."""
        data = {
            "terms": [{"name": "term1", "duration_days": 91}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_accept_boundary_durations(self):
        """Boundary values 1 and 90 are accepted."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 1},
                {"name": "term2", "duration_days": 90},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_reject_non_integer_duration(self):
        """Duration must be a whole number of days."""
        data = {
            "terms": [{"name": "term1", "duration_days": 3.5}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_empty_terms(self):
        """At least 1 term must be provided."""
        data = {
            "terms": [],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_more_than_three_terms(self):
        """At most 3 terms allowed."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 1},
                {"name": "term2", "duration_days": 7},
                {"name": "term3", "duration_days": 15},
                {"name": "term4", "duration_days": 30},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_invalid_term_name(self):
        """Only term1, term2, term3 are valid names."""
        data = {
            "terms": [{"name": "foo", "duration_days": 5}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_term4_name(self):
        """term4 is not a valid term name."""
        data = {
            "terms": [
                {"name": "term1", "duration_days": 1},
                {"name": "term2", "duration_days": 7},
                {"name": "term4", "duration_days": 15},
            ],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_valid_business_hours(self):
        """Business hours with timezone, weekdays, and time range passes."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "weekdays": [1, 2, 3, 4, 5],
                "timezone": "America/New_York",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_reject_invalid_timezone(self):
        """Timezone must be a valid IANA timezone string."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "weekdays": [1, 2, 3, 4, 5],
                "timezone": "Mars/Olympus_Mons",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_empty_weekdays(self):
        """At least one weekday must be selected."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "weekdays": [],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_invalid_weekday_number(self):
        """Weekdays must be 1-7 (ISO 8601)."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "weekdays": [0, 8],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_duplicate_weekdays(self):
        """Duplicate weekday values are rejected."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "weekdays": [1, 1, 2, 2],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_business_window_under_one_hour(self):
        """Business window must be at least 1 hour."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "09:30",
                "weekdays": [1],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_time_not_15min_granularity(self):
        """Times must be on 15-minute boundaries (HH:00, HH:15, HH:30, HH:45)."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:07",
                "end_time": "17:00",
                "weekdays": [1, 2, 3, 4, 5],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_reject_invalid_time_format(self):
        """Times must be HH:MM 24-hour format."""
        for invalid_time in ["9:00", "25:00", "09:60", "9am", "abc"]:
            with self.subTest(time=invalid_time):
                data = {
                    "terms": [{"name": "term1", "duration_days": 7}],
                    "business_hours": {
                        "enabled": True,
                        "start_time": invalid_time,
                        "end_time": "17:00",
                        "weekdays": [1],
                        "timezone": "UTC",
                    },
                }
                serializer = ROSCustomTimeframesSerializer(data=data)
                self.assertFalse(serializer.is_valid(), f"Should reject time: {invalid_time}")

    def test_reject_enabled_business_hours_missing_fields(self):
        """When enabled=True, start_time/end_time/weekdays/timezone are required."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {"enabled": True},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_midnight_spanning_business_hours(self):
        """Business hours can span midnight (e.g., night shift 22:00-06:00)."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "22:00",
                "end_time": "06:00",
                "weekdays": [1, 2, 3, 4, 5],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_business_hours_disabled_ignores_subfields(self):
        """When business_hours.enabled=False, sub-fields are not validated."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {"enabled": False},
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_reject_zero_hour_window(self):
        """start_time == end_time creates a 0-hour window, which is invalid."""
        data = {
            "terms": [{"name": "term1", "duration_days": 7}],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "09:00",
                "weekdays": [1, 2, 3, 4, 5],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertFalse(serializer.is_valid())

    def test_business_hours_with_default_terms(self):
        """Business hours can be enabled without changing default term durations.

        IMPL §10: 'Business hours without [custom] terms: Allowed (uses default terms
        with business hours filter).'
        """
        data = {
            "terms": [
                {"name": "term1", "duration_days": 1},
                {"name": "term2", "duration_days": 7},
                {"name": "term3", "duration_days": 15},
            ],
            "business_hours": {
                "enabled": True,
                "start_time": "09:00",
                "end_time": "17:00",
                "weekdays": [1, 2, 3, 4, 5],
                "timezone": "UTC",
            },
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_terms_without_business_hours_field(self):
        """Custom terms without business_hours field uses default (disabled).

        IMPL §10: 'Terms without business hours: Allowed.'
        """
        data = {
            "terms": [
                {"name": "term1", "duration_days": 30},
                {"name": "term2", "duration_days": 60},
            ],
        }
        serializer = ROSCustomTimeframesSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
