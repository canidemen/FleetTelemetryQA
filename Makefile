.PHONY: up down generate detect test triage

up:
	docker compose up -d

down:
	docker compose down

generate:
	python generate_telemetry.py

detect:
	python detect_regressions.py

test:
	python -m pytest test_detection.py -v

triage:
	python triage.py
