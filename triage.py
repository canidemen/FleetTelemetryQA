"""
triage.py — LLM triage layer for FleetTelemetryQA.

IMPORTANT: Detection is purely statistical (detect_regressions.py).
This module only reads the findings that the statistical detector already wrote
and asks Claude to summarize, rank by severity, and hypothesize root causes.
The LLM does not detect anything — it is a human-readable report generator.

API key is read from the ANTHROPIC_API_KEY environment variable.
Never put the key in this file or any file committed to the repo.
"""

import os
import sys

from dotenv import load_dotenv
import anthropic

load_dotenv()
from sqlalchemy import create_engine, text

DB_URL = os.getenv("DB_URL", "postgresql://postgres:dev@127.0.0.1:5433/fleetdb")

SYSTEM_PROMPT = """\
You are a senior QA engineer reviewing statistical regression findings from an \
autonomous-vehicle fleet telemetry system. You did NOT detect these findings — \
a separate statistical detector (robust z-score + PELT changepoint) already did \
that work and wrote the findings to a database. Your job is to:

1. Rank the findings by severity (highest z-score = most severe).
2. For each finding, write 1-2 sentences explaining what it means in plain English.
3. Propose a concise root-cause hypothesis for each finding.
4. Give an overall triage summary: which software version is the most problematic \
and what the combined picture suggests.

Be direct and concise. Use engineering language — the audience is a QA team, \
not end users. Do not repeat the raw numbers verbatim; synthesize them."""


def fetch_findings(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT detected_at, software_version, metric, value, "
            "baseline_median, baseline_mad, z_score, finding_type, detail "
            "FROM findings ORDER BY z_score DESC"
        )).fetchall()
    return rows


def format_findings(rows):
    if not rows:
        return "No findings in the database."

    lines = ["STATISTICAL FINDINGS (sorted by severity):", ""]
    for i, r in enumerate(rows, 1):
        lines.append(f"Finding #{i}")
        lines.append(f"  Version      : {r.software_version}")
        lines.append(f"  Metric       : {r.metric}")
        lines.append(f"  Type         : {r.finding_type}")
        lines.append(f"  Value        : {r.value:.5f}")
        lines.append(f"  Baseline med : {r.baseline_median:.5f}")
        lines.append(f"  Baseline MAD : {r.baseline_mad:.5f}")
        lines.append(f"  Z-score      : {r.z_score:.3f}")
        lines.append(f"  Detail       : {r.detail}")
        lines.append("")
    return "\n".join(lines)


def run_triage(findings_text):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        print("Set it with:  $env:ANTHROPIC_API_KEY = 'sk-ant-...'  (PowerShell)", file=sys.stderr)
        print("              export ANTHROPIC_API_KEY='sk-ant-...'   (bash)", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    user_message = (
        "Here are the regression findings produced by the statistical detector:\n\n"
        + findings_text
        + "\n\nPlease triage these findings."
    )

    print("=" * 70)
    print("FLEET TELEMETRY QA — LLM TRIAGE REPORT")
    print("Model: claude-opus-4-8  |  Detection: statistical (not LLM)")
    print("=" * 70)
    print()

    with client.messages.stream(
        model="claude-opus-4-8",
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text_chunk in stream.text_stream:
            print(text_chunk, end="", flush=True)

    print()
    print()
    print("=" * 70)
    print("END OF TRIAGE REPORT")
    print("NOTE: Findings were detected statistically. This report is an LLM")
    print("      summary only — it does not affect detection or scoring.")
    print("=" * 70)


def main():
    engine = create_engine(DB_URL)

    print("Fetching findings from database...")
    rows = fetch_findings(engine)
    print(f"Found {len(rows)} finding(s).\n")

    if not rows:
        print("Nothing to triage. Run 'make detect' first.")
        return

    findings_text = format_findings(rows)
    run_triage(findings_text)


if __name__ == "__main__":
    main()
