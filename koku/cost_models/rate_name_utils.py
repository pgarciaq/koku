#
# Copyright 2026 Red Hat Inc.
# SPDX-License-Identifier: Apache-2.0
#
"""Utility functions for cost model rate name generation.

This module is separate from the migration file so that generate_name()
can be tested directly and imported by both the migration and the serializer.
"""


def generate_name(rate, used_names):
    """Generate a unique name for a cost model rate.

    Strategy:
        1. Use the rate's description (truncated to 50 chars) if present.
        2. Otherwise, construct from metric + cost_type.
        3. Deduplicate with numeric suffix if the candidate already exists.

    Args:
        rate: A dict with at least 'metric' (with 'name' sub-key) and 'cost_type'.
        used_names: A set of names already in use within this cost model.

    Returns:
        A unique name string (max 50 chars).
    """
    description = rate.get("description", "")
    metric_name = rate.get("metric", {}).get("name", "unknown_metric")
    cost_type = rate.get("cost_type", "")

    if description:
        if len(description) <= 50:
            candidate = description
        else:
            candidate = description[:47] + "..."
    else:
        candidate = f"{metric_name}_{cost_type}".lower()[:47]

    if candidate not in used_names:
        return candidate

    # Deduplicate with numeric suffix (_000, _001, ...)
    base = candidate[:44]
    for i in range(1000):
        deduped = f"{base}_{i:03d}"
        if deduped not in used_names:
            return deduped

    raise ValueError(f"Could not generate unique name for rate (metric={metric_name})")
