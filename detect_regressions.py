import os

import pandas as pd
import ruptures as rpt
from sqlalchemy import create_engine, text
from datetime import datetime, timezone

DB_URL = os.getenv("DB_URL", "postgresql://postgres:dev@127.0.0.1:5433/fleetdb")
Z_SCORE_THRESHOLD = 3.0  # flag if a version is this many robust-sigmas worse than baseline
# 2 is the minimum to have any spread estimate at all. With only 2 baseline points the
# MAD is a single distance, so the threshold is fragile; more versions help a lot here.
MIN_BASELINE_VERSIONS = 2

VERSIONS_ORDER = ["v1", "v2", "v3", "v4", "v5"]


# ── schema ────────────────────────────────────────────────────────────────────

CREATE_FINDINGS = """
CREATE TABLE IF NOT EXISTS findings (
    id              BIGSERIAL PRIMARY KEY,
    detected_at     TIMESTAMP    NOT NULL,
    software_version VARCHAR(10) NOT NULL,
    metric          VARCHAR(60)  NOT NULL,
    value           FLOAT        NOT NULL,
    baseline_median FLOAT        NOT NULL,
    baseline_mad    FLOAT        NOT NULL,
    z_score         FLOAT        NOT NULL,
    finding_type    VARCHAR(20)  NOT NULL,  -- 'regression' or 'changepoint'
    detail          TEXT
);
"""


# ── metric queries ─────────────────────────────────────────────────────────────

def fetch_metrics(engine):
    query = """
    SELECT
        software_version,
        ROUND(
            (SUM(disengagement::int) /
             NULLIF(SUM(speed_mph * (0.1 / 3600.0)), 0) * 100)::numeric, 6
        ) AS disengagements_per_100_miles,
        ROUND(AVG(ABS(steering_angle))::numeric, 6) AS avg_steering_jitter,
        ROUND((AVG(autopilot_engaged::int) * 100)::numeric, 4) AS autopilot_engagement_pct
    FROM telemetry
    GROUP BY software_version
    ORDER BY software_version;
    """
    df = pd.read_sql(query, engine)
    df["software_version"] = pd.Categorical(df["software_version"], categories=VERSIONS_ORDER, ordered=True)
    return df.sort_values("software_version").reset_index(drop=True)


# ── z-score regression detection ──────────────────────────────────────────────

def detect_regressions(df):
    """
    For each metric, treat all versions before the current one as the baseline.
    Flag the current version if it is Z_SCORE_THRESHOLD sigmas worse.
    'Worse' means: higher for disengagements/jitter, lower for engagement.
    """
    findings = []
    higher_is_worse = ["disengagements_per_100_miles", "avg_steering_jitter"]
    lower_is_worse  = ["autopilot_engagement_pct"]
    all_metrics = higher_is_worse + lower_is_worse

    for i, row in df.iterrows():
        if i < MIN_BASELINE_VERSIONS:
            continue  # need at least MIN_BASELINE_VERSIONS prior versions for a reliable baseline
        baseline = df.iloc[:i]
        version = row["software_version"]

        for metric in all_metrics:
            vals = baseline[metric].astype(float)
            median = vals.median()
            mad = (vals - median).abs().median()
            if mad == 0:
                continue
            value = float(row[metric])
            # 0.6745 scales MAD to match standard deviation for normal data,
            # keeping the z-score comparable to the classical 3-sigma rule.
            z = 0.6745 * (value - median) / mad
            # flip sign so positive z always means "worse"
            if metric in lower_is_worse:
                z = -z

            if z > Z_SCORE_THRESHOLD:
                findings.append({
                    "detected_at":      datetime.now(timezone.utc),
                    "software_version": version,
                    "metric":           metric,
                    "value":            value,
                    "baseline_median":   float(median),
                    "baseline_mad":      float(mad),
                    "z_score":          round(z, 3),
                    "finding_type":     "regression",
                    "detail": (
                        f"{version} is {z:.2f} robust-sigma worse than baseline "
                        f"(value={value:.4f}, baseline median={median:.4f})"
                    ),
                })

    return findings


# ── changepoint detection ─────────────────────────────────────────────────────

def detect_changepoints(engine):
    """
    For each version, look at disengagement rate over time in 10-minute buckets.
    Flag versions where ruptures finds a clear shift mid-version.
    """
    findings = []

    time_series_query = """
    SELECT
        software_version,
        date_trunc('minute', timestamp) -
            (EXTRACT(minute FROM timestamp)::int % 10) * interval '1 minute' AS bucket,
        AVG(disengagement::int) AS disengage_rate
    FROM telemetry
    GROUP BY software_version, bucket
    ORDER BY software_version, bucket;
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(time_series_query), conn)

    for version in VERSIONS_ORDER:
        vdf = df[df["software_version"] == version]["disengage_rate"].values
        if len(vdf) < 10:
            continue

        signal = vdf.reshape(-1, 1)
        algo = rpt.Pelt(model="rbf").fit(signal)
        try:
            breakpoints = algo.predict(pen=0.5)
        except Exception:
            continue

        # ruptures always returns the last index as a breakpoint; ignore it
        interior = [b for b in breakpoints if b < len(vdf)]
        if interior:
            for bp in interior:
                before_mean = vdf[:bp].mean()
                after_mean  = vdf[bp:].mean()
                change_pct  = abs(after_mean - before_mean) / (before_mean + 1e-9) * 100
                if change_pct > 20:  # only report meaningful shifts
                    findings.append({
                        "detected_at":      datetime.now(timezone.utc),
                        "software_version": version,
                        "metric":           "disengagements_per_100_miles",
                        "value":            float(after_mean),
                        "baseline_median":   float(before_mean),
                        "baseline_mad":      float(vdf[:bp].std()),
                        "z_score":          0.0,
                        "finding_type":     "changepoint",
                        "detail": (
                            f"Changepoint at bucket {bp} in {version}: "
                            f"rate shifted from {before_mean:.5f} to {after_mean:.5f} "
                            f"({change_pct:.1f}% change)"
                        ),
                    })

    return findings


# ── save findings ──────────────────────────────────────────────────────────────

def save_findings(findings, engine):
    if not findings:
        print("No findings to save.")
        return
    df = pd.DataFrame(findings)
    df.to_sql("findings", engine, if_exists="append", index=False)
    print(f"Saved {len(df)} findings to the findings table.")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    engine = create_engine(DB_URL)

    with engine.begin() as conn:
        conn.execute(text(CREATE_FINDINGS))
        conn.execute(text("TRUNCATE findings"))

    print("Fetching metrics per version...")
    df = fetch_metrics(engine)
    print(df.to_string(index=False))

    print("\nRunning z-score regression detection...")
    regression_findings = detect_regressions(df)
    for f in regression_findings:
        print(f"  REGRESSION: {f['detail']}")

    print("\nRunning changepoint detection...")
    changepoint_findings = detect_changepoints(engine)
    for f in changepoint_findings:
        print(f"  CHANGEPOINT: {f['detail']}")

    all_findings = regression_findings + changepoint_findings
    print(f"\nTotal findings: {len(all_findings)}")
    save_findings(all_findings, engine)

    if regression_findings:
        worst = max(regression_findings, key=lambda x: x["z_score"])
        print(f"\nWorst regression: {worst['software_version']} — "
              f"{worst['metric']} at {worst['z_score']:.2f} sigma above baseline")


if __name__ == "__main__":
    main()
