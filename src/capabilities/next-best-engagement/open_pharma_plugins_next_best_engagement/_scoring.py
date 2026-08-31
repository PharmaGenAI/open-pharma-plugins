"""Weighted linear scoring engine for HCP engagement opportunities."""

from __future__ import annotations

from datetime import date

from .models import ConstraintConfig, UniverseRow


def score_hcp(
    hcp: UniverseRow,
    config: ConstraintConfig,
    tier_coverage: dict[str, float],
    today: date,
) -> tuple[float, str]:
    """Score an HCP and return (score, rationale_string)."""
    w = config.weights
    factors: list[tuple[str, float, float]] = []

    # 1. Recency gap — how overdue is this HCP?
    target_interval = config.period_days
    last_touch = _most_recent_touch(hcp)
    if last_touch:
        days_gap = (today - last_touch).days
        recency = min(days_gap / max(target_interval, 1), 1.0)
    else:
        recency = 1.0
    factors.append(("recency_gap", recency, recency * w.recency_gap))

    # 2. Tier value
    tier_scores = {"A": 1.0, "B": 0.7, "C": 0.4, "D": 0.15}
    tier_val = tier_scores.get(hcp.tier, 0.5)
    factors.append(("tier_value", tier_val, tier_val * w.tier_value))

    # 3. Engagement velocity — cold HCPs need re-engagement
    total_90d = hcp.visits_last_90d + hcp.emails_last_90d
    if total_90d == 0:
        velocity = 0.8
    elif total_90d <= 2:
        velocity = 0.5
    else:
        velocity = 0.3
    factors.append(("engagement_velocity", velocity, velocity * w.engagement_velocity))

    # 4. Channel diversity — reward recent use of more than one channel type
    touches = [
        (hcp.last_visit_date, "visit"),
        (hcp.last_email_date, "email"),
        (hcp.last_meeting_date, "meeting"),
    ]
    recent_channels = [ch for d, ch in touches if d and (today - d).days <= 90]
    if len(recent_channels) <= 1:
        diversity = 0.8 if recent_channels else 1.0
    else:
        unique = len(set(recent_channels))
        diversity = unique / len(recent_channels)
    factors.append(("channel_diversity", diversity, diversity * w.channel_diversity))

    # 5. Coverage debt — how far is this tier from its target?
    target_map = {
        "A": config.tier_a_coverage_pct,
        "B": config.tier_b_coverage_pct,
        "C": config.tier_c_coverage_pct,
        "D": config.tier_d_coverage_pct,
    }
    target = target_map.get(hcp.tier, 0.5)
    actual = tier_coverage.get(hcp.tier, 0.0)
    debt = max(0.0, target - actual) / max(target, 0.01)
    factors.append(("coverage_debt", debt, debt * w.coverage_debt))

    total_score = min(max(sum(wt for _, _, wt in factors), 0.0), 1.0)

    factors.sort(key=lambda f: f[2], reverse=True)
    parts = [f"{name}={raw:.2f} (w={weighted:.2f})" for name, raw, weighted in factors if weighted > 0.01]
    rationale = f"Tier {hcp.tier}, score {total_score:.2f}: " + ", ".join(parts)

    return total_score, rationale


def _most_recent_touch(hcp: UniverseRow) -> date | None:
    dates = [d for d in (hcp.last_visit_date, hcp.last_email_date, hcp.last_meeting_date) if d]
    return max(dates) if dates else None
