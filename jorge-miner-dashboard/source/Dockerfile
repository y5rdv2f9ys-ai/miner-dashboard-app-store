FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MINER_DASHBOARD_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app_v2.py benchmark_engine.py benchmark_profiles.py benchmark_restore.py benchmark_results.py benchmark_sessions.py discord_alerts.py miner_api.py miner_telemetry.py miner_thermal_mode.py thermal_locks.py ./
COPY static ./static
COPY tui ./tui
COPY miners_v2.json discord_alert_config.json ./defaults/
COPY entrypoint.sh /usr/local/bin/miner-dashboard-entrypoint
COPY miners-tui /usr/local/bin/miners-tui

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && chmod +x /usr/local/bin/miner-dashboard-entrypoint /usr/local/bin/miners-tui

EXPOSE 5057

ENTRYPOINT ["miner-dashboard-entrypoint"]
CMD ["python", "-u", "/app/app_v2.py"]
