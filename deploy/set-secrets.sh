#!/usr/bin/env bash
# Prompts (hidden input) for each secret and stores it as a new Secret Manager version.
# Usage: PROJECT_ID=pulse-beta deploy/set-secrets.sh [SECRET_NAME ...]
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
names=("$@"); [ ${#names[@]} -gt 0 ] || names=(DATABASE_URL MIGRATIONS_DATABASE_URL SUPABASE_JWT_SECRET FERNET_KEYS)
for n in "${names[@]}"; do
  if [ "$n" = FERNET_KEYS ]; then
    read -r -p "FERNET_KEYS: press Enter to generate a new key, or paste existing: " v
    [ -n "$v" ] || v="$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  else
    read -r -s -p "$n: " v; echo
  fi
  printf '%s' "$v" | gcloud secrets versions add "$n" --project "$PROJECT_ID" --data-file=-
done
