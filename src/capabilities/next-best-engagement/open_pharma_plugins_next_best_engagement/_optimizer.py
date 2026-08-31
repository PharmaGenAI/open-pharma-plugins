"""Two-phase greedy optimizer for engagement plan allocation."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from ._scoring import score_hcp
from ._universe import fingerprint_universe
from .models import (
    ConstraintConfig,
    EngagementPlan,
    PlanMetrics,
    PlannedEngagement,
    RepUtilization,
    TierCoverage,
    UnassignedHCP,
    UniverseRow,
)


def generate_plan(
    universe: list[UniverseRow],
    rep_info: dict[str, dict[str, Any]],
    config: ConstraintConfig,
    today: date | None = None,
    universe_generation: int = 0,
) -> EngagementPlan:
    """Generate an optimized engagement plan."""
    today = today or date.today()
    _validate_universe(universe, today)
    period_start = today
    period_end = today + timedelta(days=config.period_days)

    weeks_in_period = config.period_days / 7
    rep_remaining: dict[str, int] = {
        rep_id: int(info["capacity"] * weeks_in_period) for rep_id, info in rep_info.items()
    }

    tier_coverage_actual = {hcp.tier: 0.0 for hcp in universe}
    scored = []
    for hcp in universe:
        s, rationale = score_hcp(hcp, config, tier_coverage_actual, today)
        scored.append((s, hcp, rationale))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Coverage targets and metrics use the same pre-allocation action-eligibility
    # boundary as the planning pass: consent/gap checks plus the score threshold.
    eligible_universe = [
        hcp
        for score, hcp, _ in scored
        if _eligibility_reason(hcp, config, today) is None and score >= config.no_action_threshold
    ]
    tier_counts: dict[str, int] = {}
    for hcp in eligible_universe:
        tier_counts[hcp.tier] = tier_counts.get(hcp.tier, 0) + 1

    tier_coverage_actual = {t: 0.0 for t in tier_counts}

    engagements: list[PlannedEngagement] = []
    unassigned: list[UnassignedHCP] = []
    assigned_hcps: set[str] = set()
    no_action_reasons: dict[str, int] = {}

    target_map = {
        "A": config.tier_a_coverage_pct,
        "B": config.tier_b_coverage_pct,
        "C": config.tier_c_coverage_pct,
        "D": config.tier_d_coverage_pct,
    }

    # Phase 1: Coverage pass
    for tier in ["A", "B", "C", "D"]:
        target_count = math.ceil(target_map[tier] * tier_counts.get(tier, 0))
        tier_assigned = 0

        tier_scored = [(s, hcp, r) for s, hcp, r in scored if hcp.tier == tier and hcp.hcp_id not in assigned_hcps]
        tier_scored.sort(key=lambda x: x[0], reverse=True)

        for s, hcp, rationale in tier_scored:
            if tier_assigned >= target_count:
                break

            eligibility_reason = _eligibility_reason(hcp, config, today)
            if eligibility_reason is not None:
                _record_unassigned(hcp, eligibility_reason, unassigned, no_action_reasons)
                assigned_hcps.add(hcp.hcp_id)
                continue
            if s < config.no_action_threshold:
                _record_unassigned(hcp, "below_threshold", unassigned, no_action_reasons)
                assigned_hcps.add(hcp.hcp_id)
                continue

            result = _try_assign(
                hcp,
                s,
                rationale,
                config,
                rep_remaining,
                rep_info,
                period_start,
                period_end,
                today,
            )
            if result is None:
                _record_unassigned(hcp, "no_rep_capacity", unassigned, no_action_reasons)
                assigned_hcps.add(hcp.hcp_id)
                continue
            action, engagement = result
            if action == "no_action":
                _record_unassigned(hcp, _no_action_reason(hcp, config, today), unassigned, no_action_reasons)
                assigned_hcps.add(hcp.hcp_id)
                continue

            engagements.append(engagement)
            assigned_hcps.add(hcp.hcp_id)
            if action == "in_person_visit":
                rep_remaining[engagement.rep_id] -= 1
            tier_assigned += 1

    # Re-score remaining HCPs with updated coverage for Phase 2
    tier_assigned: dict[str, int] = {}
    for e in engagements:
        tier_assigned[e.tier] = tier_assigned.get(e.tier, 0) + 1
    tier_coverage_actual = {
        tier: tier_assigned.get(tier, 0) / count for tier, count in tier_counts.items() if count > 0
    }

    # Phase 2: Opportunity fill
    remaining = []
    for hcp in universe:
        if hcp.hcp_id not in assigned_hcps:
            s, rationale = score_hcp(hcp, config, tier_coverage_actual, today)
            remaining.append((s, hcp, rationale))
    remaining.sort(key=lambda x: x[0], reverse=True)

    for s, hcp, rationale in remaining:
        eligibility_reason = _eligibility_reason(hcp, config, today)
        if eligibility_reason is not None:
            _record_unassigned(hcp, eligibility_reason, unassigned, no_action_reasons)
            continue
        if s < config.no_action_threshold:
            _record_unassigned(hcp, "below_threshold", unassigned, no_action_reasons)
            continue

        result = _try_assign(
            hcp,
            s,
            rationale,
            config,
            rep_remaining,
            rep_info,
            period_start,
            period_end,
            today,
        )
        if result is None:
            _record_unassigned(hcp, "no_rep_capacity", unassigned, no_action_reasons)
            continue
        action, engagement = result
        if action == "no_action":
            _record_unassigned(hcp, _no_action_reason(hcp, config, today), unassigned, no_action_reasons)
            continue

        engagements.append(engagement)
        assigned_hcps.add(hcp.hcp_id)
        if action == "in_person_visit":
            rep_remaining[engagement.rep_id] -= 1

    metrics = _build_metrics(
        len(universe),
        eligible_universe,
        engagements,
        rep_info,
        rep_remaining,
        config,
        no_action_reasons,
        weeks_in_period,
        tier_counts,
        target_map,
    )

    return EngagementPlan(
        universe_fingerprint=fingerprint_universe(universe),
        universe_generation=universe_generation,
        period_start=period_start,
        period_end=period_end,
        generated_at=today.isoformat(),
        engagements=engagements,
        unassigned=unassigned,
        metrics=metrics,
    )


# ---- internal helpers ----


def _validate_universe(universe: list[UniverseRow], today: date) -> None:
    seen_hcp_ids: set[str] = set()
    for hcp in universe:
        if hcp.hcp_id in seen_hcp_ids:
            raise ValueError(f"Duplicate hcp_id: {hcp.hcp_id}")
        seen_hcp_ids.add(hcp.hcp_id)
        for field_name in ("last_visit_date", "last_email_date", "last_meeting_date"):
            touch_date = getattr(hcp, field_name)
            if touch_date is not None and touch_date > today:
                raise ValueError(f"{field_name} is future-dated relative to the planning date")


def _try_assign(
    hcp: UniverseRow,
    score: float,
    rationale: str,
    config: ConstraintConfig,
    rep_remaining: dict[str, int],
    rep_info: dict[str, dict[str, Any]],
    period_start: date,
    period_end: date,
    today: date,
) -> tuple[str, PlannedEngagement] | None:
    """Assign to the declared rep, enforcing capacity only for in-person visits."""
    action = _select_action(hcp, config, today)
    if action == "in_person_visit" and rep_remaining.get(hcp.rep_id, 0) <= 0:
        return None

    engagement = _create_engagement(
        hcp,
        action,
        hcp.rep_id,
        rep_info,
        score,
        rationale,
        period_start,
        period_end,
    )
    return action, engagement


def _select_action(
    hcp: UniverseRow,
    config: ConstraintConfig,
    today: date,
) -> str:
    """Rule cascade: pick the best action for this HCP."""
    last_visit = hcp.last_visit_date
    days_since_visit = (today - last_visit).days if last_visit else 999

    # Rule 0: Min gap — skip if contacted too recently
    most_recent = max(
        (d for d in [hcp.last_visit_date, hcp.last_email_date, hcp.last_meeting_date] if d),
        default=None,
    )
    if most_recent and (today - most_recent).days < config.min_gap_days:
        return "no_action"

    # Rule 1: High-tier + overdue for visit + phone consent → in-person visit
    if hcp.tier in ("A", "B") and days_since_visit >= 45 and hcp.consent_phone is True:
        return "in_person_visit"

    # Rule 2: Recently visited + email consent → email follow-up
    if days_since_visit < 30 and hcp.consent_email is True:
        return "approved_email"

    # Rule 3: Not recently visited + phone consent → remote meeting
    if days_since_visit >= 30 and hcp.consent_phone is True:
        return "remote_meeting"

    # Rule 4: Email fallback
    if hcp.consent_email is True:
        return "approved_email"

    # Rule 5: Phone fallback
    if hcp.consent_phone is True:
        return "in_person_visit"

    return "no_action"


def _create_engagement(
    hcp: UniverseRow,
    action: str,
    rep_id: str,
    rep_info: dict[str, dict[str, Any]],
    score: float,
    rationale: str,
    period_start: date,
    period_end: date,
) -> PlannedEngagement:
    if score >= 0.8:
        priority = 1
    elif score >= 0.6:
        priority = 2
    elif score >= 0.4:
        priority = 3
    elif score >= 0.2:
        priority = 4
    else:
        priority = 5

    rep_name = rep_info.get(rep_id, {}).get("name", rep_id)

    data: dict[str, Any] = dict(hcp.model_extra or {})
    data.update(
        {
            "hcp_id": hcp.hcp_id,
            "hcp_name": hcp.hcp_name,
            "specialty": hcp.specialty,
            "tier": hcp.tier,
            "territory_id": hcp.territory_id,
            "rep_id": rep_id,
            "rep_name": rep_name,
            "action_type": action,
            "priority": priority,
            "score": round(score, 3),
            "suggested_window_start": period_start,
            "suggested_window_end": period_end,
            "rationale": rationale,
        }
    )
    return PlannedEngagement.model_validate(data)


def _no_action_reason(hcp: UniverseRow, config: ConstraintConfig, today: date) -> str:
    """Determine why an HCP got no_action."""
    return _eligibility_reason(hcp, config, today) or "channels_exhausted"


def _eligibility_reason(hcp: UniverseRow, config: ConstraintConfig, today: date) -> str | None:
    """Return a fail-closed eligibility reason before score-based filtering."""
    most_recent = max(
        (d for d in [hcp.last_visit_date, hcp.last_email_date, hcp.last_meeting_date] if d),
        default=None,
    )
    if most_recent and (today - most_recent).days < config.min_gap_days:
        return "too_recent"
    if hcp.consent_email is True or hcp.consent_phone is True:
        return None
    if hcp.consent_email is None or hcp.consent_phone is None:
        return "missing_consent"
    if not hcp.consent_email and not hcp.consent_phone:
        return "no_consent"
    return None


def _record_unassigned(
    hcp: UniverseRow,
    reason: str,
    unassigned: list[UnassignedHCP],
    no_action_reasons: dict[str, int],
) -> None:
    no_action_reasons[reason] = no_action_reasons.get(reason, 0) + 1
    unassigned.append(UnassignedHCP(hcp_id=hcp.hcp_id, hcp_name=hcp.hcp_name, tier=hcp.tier, reason=reason))


def _build_metrics(
    total_universe: int,
    eligible_universe: list[UniverseRow],
    engagements: list[PlannedEngagement],
    rep_info: dict[str, dict[str, Any]],
    rep_remaining: dict[str, int],
    config: ConstraintConfig,
    no_action_reasons: dict[str, int],
    weeks_in_period: float,
    tier_counts: dict[str, int],
    target_map: dict[str, float],
) -> PlanMetrics:
    total_eligible = len(eligible_universe)
    total_planned = len(engagements)

    # Coverage by tier
    tier_planned: dict[str, int] = {}
    for e in engagements:
        tier_planned[e.tier] = tier_planned.get(e.tier, 0) + 1

    coverage_by_tier: dict[str, TierCoverage] = {}
    for tier in sorted(tier_counts.keys()):
        total = tier_counts[tier]
        planned = tier_planned.get(tier, 0)
        actual_pct = planned / total if total else 0.0
        coverage_by_tier[tier] = TierCoverage(
            target_pct=target_map.get(tier, 0.5),
            actual_pct=round(actual_pct, 3),
            total=total,
            planned=planned,
            gap=max(0, math.ceil(target_map.get(tier, 0.5) * total) - planned),
        )

    # Rep utilization
    rep_utilization = []
    for rep_id, info in sorted(rep_info.items()):
        capacity = int(info["capacity"] * weeks_in_period)
        assigned = capacity - rep_remaining.get(rep_id, 0)
        rep_utilization.append(
            RepUtilization(
                rep_id=rep_id,
                rep_name=info["name"],
                capacity=capacity,
                assigned=assigned,
                utilization_pct=round(assigned / capacity, 3) if capacity else 0.0,
            )
        )

    # Channel mix
    channel_mix: dict[str, int] = {}
    for e in engagements:
        channel_mix[e.action_type] = channel_mix.get(e.action_type, 0) + 1

    coverage_pct = total_planned / total_eligible if total_eligible else 0.0

    return PlanMetrics(
        total_universe=total_universe,
        total_eligible=total_eligible,
        total_planned=total_planned,
        coverage_pct=round(coverage_pct, 3),
        coverage_by_tier=coverage_by_tier,
        rep_utilization=rep_utilization,
        channel_mix=channel_mix,
        no_action_count=sum(no_action_reasons.values()),
        no_action_reasons=no_action_reasons,
    )
