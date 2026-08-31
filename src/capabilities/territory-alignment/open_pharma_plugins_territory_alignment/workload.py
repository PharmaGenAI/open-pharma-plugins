"""Shared visit-frequency and workload calculations for territory planning."""

from __future__ import annotations

from .models import HCP, Constraint

_DEFAULT_FREQUENCY_WEEKS = {"high": 2.0, "medium": 4.0, "low": 8.0}
_HOURS_PER_VISIT = {"high": 1.0, "medium": 0.75, "low": 0.5}


def frequency_weeks(segment: str, constraints: list[Constraint]) -> float:
    for constraint in constraints:
        if constraint.type == "frequency_cap" and constraint.scope == f"segment:{segment}":
            weeks = float(constraint.value)
            if weeks <= 0:
                raise ValueError(f"frequency_cap for segment {segment!r} must be greater than zero")
            return weeks
    return _DEFAULT_FREQUENCY_WEEKS[segment]


def visits_per_year(hcp: HCP, constraints: list[Constraint]) -> int:
    return max(1, round(48.0 / frequency_weeks(hcp.segment, constraints)))


def hours_per_visit(hcp: HCP) -> float:
    return _HOURS_PER_VISIT[hcp.segment]


def hcp_weekly_hours(hcp: HCP, constraints: list[Constraint]) -> float:
    return hours_per_visit(hcp) * visits_per_year(hcp, constraints) / 48.0
