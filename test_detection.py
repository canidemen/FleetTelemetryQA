import pandas as pd
import pytest
from detect_regressions import detect_regressions, VERSIONS_ORDER


def make_df(disengagements, jitter, engagement):
    """Build a minimal metrics DataFrame in the same shape fetch_metrics() returns."""
    versions = VERSIONS_ORDER[: len(disengagements)]
    df = pd.DataFrame(
        {
            "software_version": pd.Categorical(versions, categories=VERSIONS_ORDER, ordered=True),
            "disengagements_per_100_miles": disengagements,
            "avg_steering_jitter": jitter,
            "autopilot_engagement_pct": engagement,
        }
    )
    return df.sort_values("software_version").reset_index(drop=True)


def flagged_versions(findings):
    return {f["software_version"] for f in findings}


# ── test 1: one version clearly worse on steering jitter ─────────────────────

def test_obvious_jitter_regression_is_flagged():
    """A version with steering jitter far above the baseline must be caught."""
    df = make_df(
        disengagements=[0.001, 0.001, 0.001, 0.020, 0.001],
        jitter=          [3.0,   3.1,   2.9,  50.0,  3.0],
        engagement=      [95.0,  95.0,  95.0,  95.0, 95.0],
    )
    findings = detect_regressions(df)
    assert "v4" in flagged_versions(findings), (
        "v4 has jitter of 50.0 vs baseline of ~3.0 and must be flagged"
    )


# ── test 2: no false positives when all versions are well-behaved ─────────────

def test_no_findings_when_all_versions_normal():
    """When every version is within normal spread, the detector must stay silent."""
    df = make_df(
        disengagements=[0.001, 0.001, 0.0011, 0.001, 0.001],
        jitter=          [3.0,   3.1,   2.9,   3.05,  3.0],
        engagement=      [95.0,  95.1,  94.9,  95.0,  95.0],
    )
    findings = detect_regressions(df)
    assert findings == [], (
        f"Expected no findings for well-behaved data, got: {findings}"
    )


# ── test 3: planted-style pattern matching the real v4 regression ─────────────

def test_planted_v4_regression_is_caught():
    """v1-v3 tight, v4 elevated on disengagements and jitter, v5 back to normal.

    Baseline values are deliberately varied (not identical) so MAD is nonzero
    and the robust z-score can be computed. Values still approximate the real
    per-version numbers from the populated database.
    """
    df = make_df(
        disengagements=[0.0009, 0.0011, 0.0010, 0.0040, 0.0010],
        jitter=          [15.69,  15.72,  15.78,  16.84,  15.65],
        engagement=      [95.2,   95.0,   94.4,   79.9,   95.1],
    )
    findings = detect_regressions(df)
    assert "v4" in flagged_versions(findings), (
        "v4 must be flagged: its values match the planted regression in the real dataset"
    )
    assert "v5" not in flagged_versions(findings), (
        "v5 is normal and must not be flagged even though v4 is in the baseline"
    )
