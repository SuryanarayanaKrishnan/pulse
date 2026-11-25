#!/usr/bin/env bash
# Nightly: encrypted pg_dump + Fernet key -> private Object Storage bucket (instance-principal auth, no keys on disk).
# Encryption: AES-256 with a passphrase derived from the deploy SSH key, which lives in GitHub and on surya's PC,
# so backups stay readable even if the VM is gone. Keeps the newest 14 dumps.
set -euo pipefail
cd /opt/pulse
set -a; . ./.env; set +a
: "${BACKUP_PASSPHRASE:?}"
BUCKET=pulse-backups
AUTH=(--auth instance_principal)
NS="$(oci os ns get "${AUTH[@]}" --query data --raw-output)"
TS="$(date -u +%Y%m%d-%H%M)"
enc() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE; }

docker compose -f deploy/oci/docker-compose.prod.yml --env-file .env exec -T postgres pg_dump -U pulse -Fc pulse \
  | enc | oci os object put "${AUTH[@]}" -ns "$NS" -bn "$BUCKET" --name "db/pulse-$TS.dump.enc" --file - --force >/dev/null
[ -f .fernet ] && enc < .fernet | oci os object put "${AUTH[@]}" -ns "$NS" -bn "$BUCKET" --name "keys/fernet.enc" --file - --force >/dev/null

# prune: keep the newest 14 dumps
oci os object list "${AUTH[@]}" -ns "$NS" -bn "$BUCKET" --prefix db/ --all --query 'data[].name' \
  | python3 -c 'import json,sys; [print(n) for n in sorted(json.load(sys.stdin), reverse=True)[14:]]' \
  | while read -r name; do oci os object delete "${AUTH[@]}" -ns "$NS" -bn "$BUCKET" --object-name "$name" --force; done
echo "backup ok: db/pulse-$TS.dump.enc"
