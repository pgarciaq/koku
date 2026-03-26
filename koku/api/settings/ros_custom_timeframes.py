#
# Copyright 2021 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
import copy
import re
from zoneinfo import ZoneInfo

from django_tenants.utils import schema_context
from rest_framework import serializers

from api.settings.settings import DEFAULT_USER_SETTINGS
from reporting.user_settings.models import UserSettings

ROS_CUSTOM_TIMEFRAMES_KEY = "ros_custom_timeframes"

ROS_CUSTOM_TIMEFRAMES_DEFAULTS = {
    "terms": [
        {"name": "term1", "duration_days": 1},
        {"name": "term2", "duration_days": 7},
        {"name": "term3", "duration_days": 15},
    ],
    "business_hours": {"enabled": False},
}

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _parse_hhmm(s: str) -> int:
    h, m = map(int, s.split(":"))
    return h * 60 + m


def _business_window_minutes(start: str, end: str) -> int:
    sm = _parse_hhmm(start)
    em = _parse_hhmm(end)
    if sm == em:
        return 0
    if em > sm:
        return em - sm
    return (24 * 60 - sm) + em


def _validate_time_field(label: str, t: str) -> None:
    if not isinstance(t, str) or not _TIME_RE.match(t):
        raise serializers.ValidationError(f"{label} must be HH:MM in 24-hour format.")
    minute = int(t.split(":")[1])
    if minute % 15 != 0:
        raise serializers.ValidationError(f"{label} must use 15-minute granularity.")


def _validate_weekdays(weekdays) -> None:
    if not isinstance(weekdays, list) or len(weekdays) < 1:
        raise serializers.ValidationError("At least one weekday is required.")
    seen = set()
    for w in weekdays:
        if not isinstance(w, int) or w < 1 or w > 7:
            raise serializers.ValidationError("Weekdays must be integers from 1 to 7 (ISO).")
        if w in seen:
            raise serializers.ValidationError("Duplicate weekdays are not allowed.")
        seen.add(w)


def _validate_timezone(tz_name) -> None:
    if not isinstance(tz_name, str):
        raise serializers.ValidationError("timezone must be a string.")
    try:
        ZoneInfo(tz_name)
    except Exception:
        raise serializers.ValidationError("timezone must be a valid IANA timezone.") from None


class ROSCustomTimeframesSerializer(serializers.Serializer):
    terms = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=3)
    business_hours = serializers.DictField(required=False)

    def validate_terms(self, value):
        expected_names = ("term1", "term2", "term3")
        durations = []
        for i, term in enumerate(value):
            name = term.get("name")
            if name != expected_names[i]:
                raise serializers.ValidationError(f"Term at index {i} must be named {expected_names[i]}.")
            if "duration_days" not in term:
                raise serializers.ValidationError("Each term requires duration_days.")
            d = term["duration_days"]
            if type(d) is not int:
                raise serializers.ValidationError("duration_days must be an integer.")
            if d < 1 or d > 90:
                raise serializers.ValidationError("duration_days must be between 1 and 90.")
            durations.append(d)
        if len(set(durations)) != len(durations):
            raise serializers.ValidationError("Term durations must be distinct.")
        for a, b in zip(durations, durations[1:]):
            if not a < b:
                raise serializers.ValidationError("Terms must be ordered by strictly ascending duration.")
        return value

    def validate_business_hours(self, value):
        if value is None:
            return {"enabled": False}
        enabled = value.get("enabled", False)
        if not enabled:
            return {"enabled": False}
        required = ("start_time", "end_time", "weekdays", "timezone")
        for k in required:
            if k not in value:
                raise serializers.ValidationError(f"When business_hours.enabled is True, {k} is required.")
        start, end = value["start_time"], value["end_time"]
        _validate_time_field("start_time", start)
        _validate_time_field("end_time", end)
        if _business_window_minutes(start, end) < 60:
            raise serializers.ValidationError("Business window must be at least 1 hour.")
        weekdays = value["weekdays"]
        _validate_weekdays(weekdays)
        tz_name = value["timezone"]
        _validate_timezone(tz_name)
        return {
            "enabled": True,
            "start_time": start,
            "end_time": end,
            "weekdays": weekdays,
            "timezone": tz_name,
        }

    def validate(self, attrs):
        bh = attrs.get("business_hours")
        if bh is None:
            attrs["business_hours"] = {"enabled": False}
        return attrs


def get_ros_custom_timeframes(schema_name: str) -> dict:
    with schema_context(schema_name):
        row = UserSettings.objects.first()
        if not row or ROS_CUSTOM_TIMEFRAMES_KEY not in row.settings:
            return copy.deepcopy(ROS_CUSTOM_TIMEFRAMES_DEFAULTS)
        return copy.deepcopy(row.settings[ROS_CUSTOM_TIMEFRAMES_KEY])


def set_ros_custom_timeframes(schema_name: str, config: dict) -> None:
    with schema_context(schema_name):
        row = UserSettings.objects.first()
        if not row:
            settings_dict = {**DEFAULT_USER_SETTINGS, ROS_CUSTOM_TIMEFRAMES_KEY: copy.deepcopy(config)}
            UserSettings.objects.create(settings=settings_dict)
        else:
            settings = copy.deepcopy(row.settings)
            settings[ROS_CUSTOM_TIMEFRAMES_KEY] = copy.deepcopy(config)
            row.settings = settings
            row.save()
