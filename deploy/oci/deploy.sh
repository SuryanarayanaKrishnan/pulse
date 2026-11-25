#!/usr/bin/env bash
# Rebuild and restart the stack from the checkout in /opt/pulse (synced by CI, or git-pulled by hand).
# Idempotent. Run on the VM.
set -euo pipefail
cd /opt/pulse
[ -f .env ] || { echo "missing /opt/pulse/.env"; exit 1; }

# FERNET_KEYS must stay stable (it encrypts stored tokens): generate once, keep it on the VM.
if ! grep -qE '^FERNET_KEYS=.+' .env; then
  [ -f .fernet ] || docker run --rm python:3.13-slim sh -c \
    "pip -q install cryptography >/dev/null 2>&1 && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'" > .fernet
  sed -i '/^FERNET_KEYS=/d' .env && echo "FERNET_KEYS=$(cat .fernet)" >> .env
fi
# Local Postgres password: generated once, kept on the VM.
[ -f .pgpass ] || head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' > .pgpass
sed -i '/^POSTGRES_PASSWORD=/d' .env && echo "POSTGRES_PASSWORD=$(cat .pgpass)" >> .env
chmod 600 .env .fernet .pgpass 2>/dev/null || true

# Nightly off-VM backup (03:10 UTC = 07:10 Dubai), logged to /var/log/pulse-backup.log
echo '10 3 * * * ubuntu /opt/pulse/deploy/oci/backup.sh >> /var/log/pulse-backup.log 2>&1' | sudo tee /etc/cron.d/pulse-backup >/dev/null
sudo touch /var/log/pulse-backup.log && sudo chown ubuntu /var/log/pulse-backup.log

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
