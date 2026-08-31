"""Operator guidance for the Competitive Intelligence evidence-run workflow."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_skill_recommends_one_run_for_report_and_timeline():
    skill = (ROOT / "src/capabilities/competitive-intelligence/skill/SKILL.md").read_text()

    refresh = skill.index("ci_refresh")
    report = skill.index("ci_report run_id=")
    timeline = skill.index("ci_timeline run_id=")
    assert refresh < report < timeline
    for status in ("complete", "partial", "failed", "not_configured", "not_applicable"):
        assert f"`{status}`" in skill


def test_ci_guidance_does_not_claim_safety_alert_coverage():
    paths = [
        ROOT / "src/capabilities/competitive-intelligence/skill/SKILL.md",
        ROOT / "cookbooks/competitive-intelligence/usage.md",
        ROOT / "README.md",
    ]
    text = "\n".join(path.read_text().lower() for path in paths)
    assert "safety alert" not in text
    assert "safety_alert" not in text
