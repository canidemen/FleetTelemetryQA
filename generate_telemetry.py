import os

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine

DB_URL = os.getenv("DB_URL", "postgresql://postgres:dev@127.0.0.1:5433/fleetdb")

VERSION_PARAMS = {
    "v1": {"disengage_prob": 0.001,  "steering_jitter": 3.0},
    "v2": {"disengage_prob": 0.001,  "steering_jitter": 2.8},
    "v3": {"disengage_prob": 0.0012, "steering_jitter": 3.1},
    "v4": {"disengage_prob": 0.005,  "steering_jitter": 7.0},  # planted regression
    "v5": {"disengage_prob": 0.001,  "steering_jitter": 3.0},
}

NUM_VEHICLES = 50
SESSIONS_PER_VEHICLE = 10
READINGS_PER_SESSION = 3000   # 5 minutes at 10 Hz
COOLDOWN_FRAMES = 50          # autopilot stays off 5 seconds after disengagement


def generate_session(version, vehicle_id, session_index, params):
    session_start = datetime(2024, 1, 1) + timedelta(
        hours=list(VERSION_PARAMS.keys()).index(version) * 24 * 7
        + int(vehicle_id.split("_")[1]) * SESSIONS_PER_VEHICLE
        + session_index
    )

    rows = []
    speed = float(np.random.uniform(30, 60))
    steering = float(np.random.uniform(-5, 5))
    autopilot_engaged = True
    cooldown = 0

    for t in range(READINGS_PER_SESSION):
        timestamp = session_start + timedelta(seconds=t * 0.1)

        speed += float(np.random.normal(0, 0.5))
        speed = float(np.clip(speed, 20, 75))

        steering += float(np.random.normal(0, params["steering_jitter"]))
        steering = float(np.clip(steering, -30, 30))

        acceleration = float(np.random.normal(0, 0.8))

        disengagement = False
        if cooldown > 0:
            cooldown -= 1
            autopilot_engaged = False
        elif autopilot_engaged:
            if np.random.random() < params["disengage_prob"]:
                disengagement = True
                autopilot_engaged = False
                cooldown = COOLDOWN_FRAMES
        else:
            autopilot_engaged = True

        rows.append({
            "timestamp":         timestamp,
            "vehicle_id":        vehicle_id,
            "software_version":  version,
            "speed_mph":         round(speed, 2),
            "steering_angle":    round(steering, 3),
            "acceleration":      round(acceleration, 3),
            "autopilot_engaged": autopilot_engaged,
            "disengagement":     disengagement,
        })

    return rows


def generate_all():
    all_rows = []
    vehicles = [f"car_{i:03d}" for i in range(NUM_VEHICLES)]

    for version, params in VERSION_PARAMS.items():
        print(f"Generating {version}...")
        for vehicle_id in vehicles:
            for session_index in range(SESSIONS_PER_VEHICLE):
                all_rows.extend(generate_session(version, vehicle_id, session_index, params))

    df = pd.DataFrame(all_rows)
    print(f"\nGenerated {len(df):,} rows")
    print(df.groupby("software_version")[["disengagement", "autopilot_engaged"]].mean().round(4))
    return df


def load_to_db(df):
    engine = create_engine(DB_URL)
    print("\nLoading to Postgres...")
    df.to_sql(
        name="telemetry",
        con=engine,
        if_exists="append",
        index=False,
        chunksize=10_000,
    )
    result = pd.read_sql(
        "SELECT software_version, COUNT(*) AS rows FROM telemetry GROUP BY software_version ORDER BY software_version",
        engine,
    )
    print("\nRow counts per version:")
    print(result.to_string(index=False))


if __name__ == "__main__":
    df = generate_all()
    load_to_db(df)
    print("\nDone.")
