FROM python:3.12-slim
WORKDIR /opt/glasses/server/telemetry
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .
# Configuration comes from the environment (TECH-01 section 8); no secrets are baked in.
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$TELEMETRY_BIND\" --port 8000 --ws-max-size 1048576 --log-level \"${LOG_LEVEL:-info}\""]
