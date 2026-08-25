#!/usr/bin/env bash
# Create the first owner account using the machine API key.
# Usage: ./deploy/bootstrap_owner.sh <email> <password> [region]
set -euo pipefail

EMAIL="${1:?Usage: bootstrap_owner.sh <email> <password> [region]}"
PASSWORD="${2:?Usage: bootstrap_owner.sh <email> <password> [region]}"
REGION="${3:-us-central1}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ADMIN_API_KEY="$(cat "$REPO_ROOT/deploy/.admin_api_key")"
SERVICE_URL="$(gcloud run services describe cumulus-knowledge --region "$REGION" --format='value(status.url)')"

curl -sS -X POST "$SERVICE_URL/api/v1/users" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"role\":\"owner\",\"display_name\":\"Owner\"}"
echo
echo "Owner created. Sign in at the admin app with $EMAIL."
