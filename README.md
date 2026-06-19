# FleetTelemetryQA

This project simulates autonomous-driving telemetry across five software versions, loads
roughly 7.7 million rows into Postgres, and then runs two independent statistical methods
to detect quality regressions between versions. One version (v4) has a deliberately
planted regression: its disengagement probability is five times higher than the baseline
and its steering jitter is elevated. The goal is to verify that both detectors catch v4
reliably and that they agree, which gives stronger confidence than either method alone.

---

## Architecture

```
generate_telemetry.py
        |
        v
  Postgres (fleetdb)
     telemetry table
        |
        +---> detect_regressions.py ---> findings table ---> triage.py (LLM summary)
        |
        +---> metrics_service/metrics.py
                        |
                        v
                  Prometheus :9090
                        |
                        v
                   Grafana :3000
```

`generate_telemetry.py` runs once to populate `telemetry`. `detect_regressions.py` reads
aggregate metrics from `telemetry`, runs the statistical checks, and writes results to
`findings`. The metrics service is a separate long-running process that re-reads Postgres
every 15 seconds and exposes Prometheus gauges, including a `fleet_regression_flagged`
gauge that lights up once detection has run.

---

## Per-version metrics (real data, 7.7M rows total)

These numbers come from a live query against the populated database. v4 is the planted
regression.

| Version | Rows      | Disengage rate | Avg steering jitter | Autopilot % |
|---------|-----------|---------------|---------------------|-------------|
| v1      | 1,708,000 | 0.0010        | 15.693              | 95.1%       |
| v2      | 1,500,000 | 0.0010        | 15.720              | 95.1%       |
| v3      | 1,500,000 | 0.0011        | 15.775              | 94.4%       |
| v4      | 1,500,000 | 0.0040        | 16.844              | 79.9%       |
| v5      | 1,500,000 | 0.0010        | 15.653              | 95.1%       |

v4 disengages four times as often as the baseline, has noticeably higher steering jitter,
and drops autopilot engagement from ~95% to 79.9%.

---

## How detection works

**Method 1: robust z-score against a rolling baseline**

For each version, the prior versions form the baseline. The detector computes the median
and median absolute deviation (MAD) of each metric across those baseline versions, then
calculates a robust z-score: `0.6745 * (value - median) / MAD`. A version is flagged if
its z-score exceeds 3.0 on any metric where a higher value means worse behavior
(disengagements per 100 miles, steering jitter) or a lower value means worse behavior
(autopilot engagement percentage).

Median and MAD are used instead of mean and standard deviation because a single bad
baseline version would inflate the standard deviation and make subsequent regressions
harder to catch. With robust statistics, one contaminated point cannot blow up the spread.

**Method 2: PELT changepoint detection within a version**

For each software version, disengagement rate is computed in 10-minute time buckets and
passed to the PELT algorithm from the `ruptures` library. PELT looks for points where the
signal level shifts abruptly. A changepoint is reported only if the shift exceeds 20%,
to filter noise.

**Why two methods**

The z-score check compares versions against each other. PELT looks for instability inside
a single version. They are independent, so if both flag v4 the finding is much more
credible than if only one did. In practice on this dataset, both catch v4, and the
z-scores are well above threshold (several sigma for disengagements and jitter).

---

## Running the project

**Prerequisites:** Docker with Compose, Python 3.11+.

```bash
# 1. Start Postgres, the metrics exporter, Prometheus, and Grafana
docker compose up -d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Generate telemetry and load to Postgres (~7.7M rows, takes a few minutes)
python generate_telemetry.py

# 4. Run statistical detection and write findings to Postgres
python detect_regressions.py
```

After step 4 the findings table is populated. The metrics exporter picks up the
`fleet_regression_flagged` gauge automatically on its next 15-second poll.

**Step 5 (optional): LLM triage**

```bash
# Set your Anthropic API key — never put this in a file you commit
$env:ANTHROPIC_API_KEY = "sk-ant-..."   # PowerShell
# export ANTHROPIC_API_KEY="sk-ant-..." # bash

python triage.py
# or: make triage
```

`triage.py` reads the `findings` table and asks Claude (claude-opus-4-8) to rank findings
by severity, explain them in plain English, and propose root-cause hypotheses. The LLM
is a **report generator only** — all detection is done statistically by `detect_regressions.py`.
The LLM output does not affect the findings table or any gauge.

**Service URLs**

| Service    | URL                    |
|------------|------------------------|
| Grafana    | http://localhost:3000  |
| Prometheus | http://localhost:9090  |
| Metrics    | http://localhost:8000/metrics |

Grafana default credentials: `admin` / `admin`.

**Or use make**

```bash
make up        # docker compose up -d
make generate  # python generate_telemetry.py
make detect    # python detect_regressions.py
make triage    # python triage.py  (requires ANTHROPIC_API_KEY env var)
make test      # pytest test_detection.py -v
```

---

## Limitations and next steps

- The planted regression in v4 is large enough to be obvious. A subtler regression
  (disengage_prob of 0.0015 instead of 0.005) would require more data or a tighter
  baseline to catch reliably.
- The PELT penalty (0.5) was chosen by inspection. It works here but would need tuning
  for noisier real-world signals.
- Five software versions is too few for the baseline to be statistically stable. With only
  two or three prior points, the robust z-score is sensitive to the exact spread of those
  points.
- The metrics service has no retry logic. If Postgres is slow to start, the container
  crashes and does not restart. A health check in `docker-compose.yml` would fix this.
- There are no alerts wired in Prometheus/Grafana yet. The `fleet_regression_flagged`
  gauge is queryable but does not page anyone.
