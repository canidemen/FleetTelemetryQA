import time
import os
from sqlalchemy import create_engine, text
from prometheus_client import start_http_server, Gauge

DB_URL = os.getenv("DB_URL", "postgresql://postgres:dev@postgres:5432/fleetdb")
SCRAPE_INTERVAL = int(os.getenv("SCRAPE_INTERVAL", "15"))

engine = create_engine(DB_URL)

disengagements = Gauge(
    "fleet_disengagements_per_100_miles",
    "Disengagements per 100 miles",
    ["software_version"],
)
steering_jitter = Gauge(
    "fleet_avg_steering_jitter",
    "Average absolute steering angle",
    ["software_version"],
)
autopilot_engagement = Gauge(
    "fleet_autopilot_engagement_pct",
    "Autopilot engagement percentage",
    ["software_version"],
)
regression_flag = Gauge(
    "fleet_regression_flagged",
    "1 if this version has an active regression finding, 0 otherwise",
    ["software_version"],
)

METRICS_QUERY = text("""
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
""")

FINDINGS_QUERY = text("""
    SELECT DISTINCT software_version
    FROM findings
    WHERE finding_type = 'regression'
""")


def refresh():
    with engine.connect() as conn:
        rows = conn.execute(METRICS_QUERY).fetchall()
        flagged = {r[0] for r in conn.execute(FINDINGS_QUERY).fetchall()}

    for row in rows:
        version = row[0]
        disengagements.labels(software_version=version).set(float(row[1]))
        steering_jitter.labels(software_version=version).set(float(row[2]))
        autopilot_engagement.labels(software_version=version).set(float(row[3]))
        regression_flag.labels(software_version=version).set(1 if version in flagged else 0)


if __name__ == "__main__":
    start_http_server(8000)
    print("Metrics server running on :8000")
    while True:
        try:
            refresh()
        except Exception as e:
            print(f"Error refreshing metrics: {e}")
        time.sleep(SCRAPE_INTERVAL)
