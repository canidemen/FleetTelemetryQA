CREATE TABLE IF NOT EXISTS telemetry (
    id                 BIGSERIAL PRIMARY KEY,
    timestamp          TIMESTAMP        NOT NULL,
    vehicle_id         VARCHAR(20)      NOT NULL,
    software_version   VARCHAR(10)      NOT NULL,
    speed_mph          FLOAT            NOT NULL,
    steering_angle     FLOAT            NOT NULL,
    acceleration       FLOAT            NOT NULL,
    autopilot_engaged  BOOLEAN          NOT NULL,
    disengagement      BOOLEAN          NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_telemetry_version   ON telemetry (software_version);
CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry (timestamp);
