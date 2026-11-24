#!/usr/bin/env bash
# Pull latest main, rebuild, migrate and restart. Idempotent. Run on the VM.
set -euo pipefail
cd /opt/pulse
git fetch -q origin main && git reset -q --hard origin/main
cd deploy/oci
docker compose -f docker-compose.prod.yml --env-file /opt/pulse/.env up -d --build --remove-orphans
docker image prune -f >/dev/null
for _ in $(seq 1 30); do
  docker compose -f docker-compose.prod.yml exec -T api python -c \
    "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2)" >/dev/null 2>&1 \
    && { echo "api up"; break; }
  sleep 2
done
docker compose -f docker-compose.prod.yml ps
