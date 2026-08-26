#!/usr/bin/env bash
# One-command deploy of the Cumulus knowledge layer.
# Prereqs: `gcloud auth login` and `firebase login` completed; billing enabled on the project.
#
# Usage: ./deploy/deploy.sh <PROJECT_ID> [REGION]
set -euo pipefail

PROJECT_ID="${1:?Usage: deploy.sh <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
SERVICE="cumulus-knowledge"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Log to a file *and* the terminal from inside the script, so nobody has to
# pipe it. `./deploy.sh ... | tee log` looks harmless but silently discards the
# script's exit status in favour of tee's, which turns a hard failure into an
# apparent success. Doing it here keeps `$?` meaningful for the caller.
LOG_FILE="${LOG_FILE:-$REPO_ROOT/deploy/last-deploy.log}"
exec > >(tee "$LOG_FILE") 2>&1

# ---- Preflight --------------------------------------------------------------
#
# Both credentials are checked before anything is built or pushed. gcloud
# expires under org session-control policy and cannot prompt for reauth in a
# container, so without this the run dies partway through `gcloud config set`
# with a message buried in output. Firebase is worse: it is not needed until
# the very last step, so a stale token wastes a Cloud Run deploy and a full
# Flutter build before failing.

fail() {
  echo
  echo "=========================================================="
  echo "PREFLIGHT FAILED — nothing has been deployed."
  echo
  printf '%s\n' "$@"
  echo "=========================================================="
  exit 1
}

# Exported before the checks below, not after: the Firebase CLI authenticates
# off gcloud Application Default Credentials, and firebase.googleapis.com
# rejects user ADC unless a quota project is set. Checking Firebase before
# setting this fails even when the credentials are perfectly good.
export GOOGLE_CLOUD_QUOTA_PROJECT="$PROJECT_ID"

echo "==> Checking credentials"
if ! gcloud auth print-access-token >/dev/null 2>&1; then
  fail \
    "gcloud credentials are missing or expired." \
    "" \
    "  Interactive shell:  gcloud auth login" \
    "" \
    "  Or a service account, which does not expire:" \
    "    gcloud auth activate-service-account --key-file=KEY.json" \
    "    export GOOGLE_APPLICATION_CREDENTIALS=\$PWD/KEY.json"
fi

if ! firebase projects:list >/dev/null 2>&1; then
  fail \
    "Firebase CLI is not authenticated (or cannot reach the API)." \
    "" \
    "  Interactive shell:  firebase login" \
    "  CI / headless:      export FIREBASE_TOKEN=... (firebase login:ci)"
fi

ACTIVE_ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
echo "==> Authenticated as: ${ACTIVE_ACCOUNT:-unknown}"

echo "==> Project: $PROJECT_ID  Region: $REGION"
gcloud config set project "$PROJECT_ID" --quiet

echo "==> Enabling required APIs"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  firebase.googleapis.com \
  identitytoolkit.googleapis.com \
  --quiet

echo "==> Ensuring Firebase is attached to the project"
# Quota project is exported during preflight — firebase.googleapis.com rejects
# user ADC without it.
if ! firebase projects:list | grep -q "$PROJECT_ID"; then
  # No `|| true`: a silent failure here leaves the project invisible in the
  # Firebase console and every step below it broken.
  firebase projects:addfirebase "$PROJECT_ID"
fi

echo "==> Ensuring Firestore database exists"
if ! gcloud firestore databases describe --database='(default)' >/dev/null 2>&1; then
  gcloud firestore databases create --location="$REGION" --quiet
fi

echo "==> Generating ADMIN_API_KEY (stored in deploy/.admin_api_key, gitignored)"
KEY_FILE="$REPO_ROOT/deploy/.admin_api_key"
if [[ ! -f "$KEY_FILE" ]]; then
  head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 40 > "$KEY_FILE"
fi
ADMIN_API_KEY="$(cat "$KEY_FILE")"

# Semantic search calls Vertex AI with the revision's own service account, so
# there is no key to manage — but the API has to be on and the account needs
# aiplatform.user. Set EMBEDDING_BACKEND=none to deploy keyword-only search and
# skip both.
EMBEDDING_BACKEND="${EMBEDDING_BACKEND:-vertex}"

# The generative half of search: the planner behind /api/v1/ask. Same client
# library, same service account, same aiplatform.user role as embeddings — so
# it costs no extra credential, only tokens. Set ANSWER_BACKEND=none to deploy
# the endpoint in its retrieval-only form, where it returns ranked documents
# and labels itself as such rather than generating prose.
ANSWER_BACKEND="${ANSWER_BACKEND:-vertex}"

# Both current models (gemini-embedding-2, gemini-3.5-flash-lite) are served
# only from the `global` endpoint; a regional one 404s. This is deliberately
# NOT $REGION — Cloud Run and Firestore stay regional, only the model calls
# are global. Override if you pin back to a regional model such as
# text-embedding-005.
VERTEX_MODEL_LOCATION="${VERTEX_MODEL_LOCATION:-global}"

if [[ "$EMBEDDING_BACKEND" == "vertex" || "$ANSWER_BACKEND" == "vertex" ]]; then
  echo "==> Enabling Vertex AI (embeddings: $EMBEDDING_BACKEND, answering: $ANSWER_BACKEND, endpoint: $VERTEX_MODEL_LOCATION)"
  gcloud services enable aiplatform.googleapis.com --quiet

  RUNTIME_SA="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
  echo "==> Granting aiplatform.user to $RUNTIME_SA"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="roles/aiplatform.user" \
    --condition=None --quiet >/dev/null
fi

echo "==> Deploying server to Cloud Run"
# Collections whose *row shape* changed with the flow rewrite, pinned to a
# generation. The seeds only run against an empty collection, so pointing at
# the old ones would leave stale rows in place and skip the seed that builds
# the graph, the runs and the tasks that go with them — a deployment that looks
# fine and demonstrates nothing. Bump the suffix whenever a stored shape
# changes; the old generation stays readable in Firestore rather than being
# migrated in place.
#
# Set them here rather than by hand after the fact, because --set-env-vars
# *replaces* the whole environment: an override applied to a live revision is
# silently dropped by the next deploy.
COLLECTION_GENERATION="${COLLECTION_GENERATION:-v3}"
GENERATION_ENV="TASKS_COLLECTION=tasks_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,REIMBURSEMENTS_COLLECTION=reimbursements_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,PAYABLES_COLLECTION=payables_$COLLECTION_GENERATION"

echo "==> Pinning reshaped collections to generation $COLLECTION_GENERATION"

gcloud run deploy "$SERVICE" \
  --source "$REPO_ROOT/server" \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "STORAGE_BACKEND=firestore,AUTH_BACKEND=firebase,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,ADMIN_API_KEY=$ADMIN_API_KEY,EMBEDDING_BACKEND=$EMBEDDING_BACKEND,EMBEDDING_LOCATION=$VERTEX_MODEL_LOCATION,ANSWER_BACKEND=$ANSWER_BACKEND,ANSWER_LOCATION=$VERTEX_MODEL_LOCATION,$GENERATION_ENV" \
  --quiet

SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo "==> Service URL: $SERVICE_URL"

echo "==> Updating PUBLIC_BASE_URL env var"
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "PUBLIC_BASE_URL=$SERVICE_URL" --quiet

echo "==> Configuring Flutter app for Firebase (web)"
cd "$REPO_ROOT/admin_app"
if ! command -v flutterfire >/dev/null; then
  dart pub global activate flutterfire_cli
  export PATH="$PATH:$HOME/.pub-cache/bin"
fi
flutterfire configure --project="$PROJECT_ID" --platforms=web --yes

echo "==> Building Flutter web app"
flutter build web --release \
  --dart-define=API_BASE_URL="$SERVICE_URL"

echo "==> Deploying to Firebase Hosting"
cd "$REPO_ROOT"
cat > .firebaserc <<EOF
{
  "projects": { "default": "$PROJECT_ID" }
}
EOF
# Rules ship with hosting: they are what keeps the client SDKs out of Firestore
# directly, and the web API key in the bundle is public.
firebase deploy --only hosting,firestore:rules --project "$PROJECT_ID"

echo
echo "=========================================================="
echo "Deployed."
echo "  API + MCP:   $SERVICE_URL   (MCP endpoint: $SERVICE_URL/mcp/)"
echo "  Admin app:   https://$PROJECT_ID.web.app"
echo
echo "Next: enable Email/Password sign-in (one-time, console UI):"
echo "  https://console.firebase.google.com/project/$PROJECT_ID/authentication/providers"
echo
echo "Then bootstrap your first owner:"
echo "  ./deploy/bootstrap_owner.sh you@example.com 'a-strong-password'"
echo "=========================================================="
