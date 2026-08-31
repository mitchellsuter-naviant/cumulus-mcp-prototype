#!/usr/bin/env bash
# Create the first owner account using the machine API key.
# Usage: ./deploy/bootstrap_owner.sh <email> <password> [region]
set -euo pipefail

EMAIL="${1:?Usage: bootstrap_owner.sh <email> <password> [region]}"
PASSWORD="${2:?Usage: bootstrap_owner.sh <email> <password> [region]}"
REGION="${3:-us-central1}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ADMIN_API_KEY="$(cat "$REPO_ROOT/deploy/.admin_api_key")"
# The direct Cloud Run URL is not publicly invokable on a project with the
# `iam.allowedPolicyMemberDomains` org policy (see deploy.sh) — Firebase
# Hosting's rewrite is the reachable path, same as everything else public.
PROJECT_ID="$(python3 -c "import json; print(json.load(open('$REPO_ROOT/.firebaserc'))['projects']['default'])")"
SERVICE_URL="https://$PROJECT_ID.web.app"

curl -sS -X POST "$SERVICE_URL/api/v1/users" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"role\":\"owner\",\"display_name\":\"Owner\"}"
echo
echo "Owner created. Sign in at the admin app with $EMAIL."
