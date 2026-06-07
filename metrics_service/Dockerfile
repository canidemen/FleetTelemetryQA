FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir sqlalchemy psycopg2-binary prometheus-client
COPY metrics.py .
CMD ["python", "metrics.py"]
