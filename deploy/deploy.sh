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

# Every ModelCall in the system — the document readers, the HR agent, the
# /ask planner. Same client library, same service account, same
# aiplatform.user role as embeddings, so it costs no extra credential, only
# tokens. Set MODEL_BACKEND=none to deploy on the deterministic local readers
# and a retrieval-only /ask, which labels itself as such rather than
# pretending to generate.
MODEL_BACKEND="${MODEL_BACKEND:-vertex}"

# Both current models (gemini-embedding-2, gemini-3.5-flash-lite) are served
# only from the `global` endpoint; a regional one 404s. This is deliberately
# NOT $REGION — Cloud Run and Firestore stay regional, only the model calls
# are global. Override if you pin back to a regional model such as
# text-embedding-005.
VERTEX_MODEL_LOCATION="${VERTEX_MODEL_LOCATION:-global}"

# The default compute service account, computed once: it runs the container
# (so it is the identity Vertex calls go out under) and, below, it is also the
# identity the scheduler authenticates *as* to call the container back in —
# two unrelated grants on one principal, not because they are related, but
# because it is the one service account already on every revision.
RUNTIME_SA="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"

if [[ "$EMBEDDING_BACKEND" == "vertex" || "$MODEL_BACKEND" == "vertex" ]]; then
  echo "==> Enabling Vertex AI (embeddings: $EMBEDDING_BACKEND, models: $MODEL_BACKEND, endpoint: $VERTEX_MODEL_LOCATION)"
  gcloud services enable aiplatform.googleapis.com --quiet

  echo "==> Granting aiplatform.user to $RUNTIME_SA"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="roles/aiplatform.user" \
    --condition=None --quiet >/dev/null
fi

# ---- The estate ------------------------------------------------------------
#
# Which live customer environment this deployment pulls from. Machinery-config,
# not map: a Mapping never names a project, and neither does anything in
# `server/app` — it arrives here, at the deployment boundary.
#
# Deliberately no default. The estate project is per-developer (the demo
# ecosystem's `.firebaserc` is git-ignored for the same reason), and guessing
# one would point a deployment at somebody else's data. Empty is honest: the
# world comes up empty and every screen says so, because the mock seed world
# was deleted when the estate became the only world.
ESTATE_PROJECT="${ESTATE_PROJECT:-}"
ESTATE_NAMESPACE="${ESTATE_NAMESPACE:-naviant}"

# Writes to the customer's systems are off unless somebody says otherwise, in
# writing, per deployment. There is no undo on the far side of an Action.
ESTATE_WRITES_ENABLED="${ESTATE_WRITES_ENABLED:-false}"

if [[ -z "$ESTATE_PROJECT" ]]; then
  echo "==> No ESTATE_PROJECT set — deploying with no estate registered."
  echo "    The world will be empty until one is configured. To connect:"
  echo "      ESTATE_PROJECT=cumulus-demo-<name> ./deploy/deploy.sh $PROJECT_ID"
else
  echo "==> Estate: $ESTATE_PROJECT / $ESTATE_NAMESPACE (writes: $ESTATE_WRITES_ENABLED)"
fi

# The connector is a client of its own server, so it needs a credential for the
# door like any other feed. `CONNECTOR_DELIVER_URL` is left unset on purpose —
# it falls back to PUBLIC_BASE_URL, which is set to this service's own URL
# below, so the two can never disagree.
ESTATE_ENV="ESTATE_PROJECT=$ESTATE_PROJECT,ESTATE_NAMESPACE=$ESTATE_NAMESPACE"
ESTATE_ENV="$ESTATE_ENV,ESTATE_WRITES_ENABLED=$ESTATE_WRITES_ENABLED"
ESTATE_ENV="$ESTATE_ENV,CONNECTOR_API_KEY=$ADMIN_API_KEY"
# The in-server pull loop stays off in a deployment: Scheduling is a sourced
# capability, and Cloud Scheduler drives the pull below. Nothing here stays warm.
ESTATE_ENV="$ESTATE_ENV,ESTATE_PULL_INTERVAL_SECONDS=0"

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
COLLECTION_GENERATION="${COLLECTION_GENERATION:-v4}"
GENERATION_ENV="TASKS_COLLECTION=tasks_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,REIMBURSEMENTS_COLLECTION=reimbursements_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,PAYABLES_COLLECTION=payables_$COLLECTION_GENERATION"
# v4 pins the world itself, not only the three reshaped tables. The generation
# before it holds a world that no longer exists: a seeded mock customer with
# its own vendors, staff and personas, deleted when the estate became the only
# source of business data. Those rows are not stale in shape, they are stale in
# *subject* — a graph carrying `acme-industrial` beside a synced counterparty
# book would be a deployment that demonstrates a contradiction. So the graph,
# the journals and the two append-only logs move with it, and the old world
# stays readable in Firestore rather than being migrated or deleted.
GENERATION_ENV="$GENERATION_ENV,GRAPH_OBJECTS_COLLECTION=graph_objects_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,GRAPH_LINKS_COLLECTION=graph_links_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,RUNS_COLLECTION=flow_runs_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,LEDGER_COLLECTION=curation_ledger_$COLLECTION_GENERATION"
GENERATION_ENV="$GENERATION_ENV,EVENTS_COLLECTION=events_$COLLECTION_GENERATION"

echo "==> Pinning reshaped collections to generation $COLLECTION_GENERATION"

# 600s, not the 300s default: a full estate pull walks every registered feed
# in one request, and the identity/disagreement sweeps scan the whole book.
# This is Cloud Run's own timeout, and it is the reason the three bulk jobs
# below call this URL directly rather than through Hosting — Hosting's rewrite
# proxy carries a fixed ~60s cap that nothing in firebase.json can raise.
gcloud run deploy "$SERVICE" \
  --source "$REPO_ROOT/server" \
  --region "$REGION" \
  --allow-unauthenticated \
  --timeout=600 \
  --set-env-vars "STORAGE_BACKEND=firestore,AUTH_BACKEND=firebase,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,ADMIN_API_KEY=$ADMIN_API_KEY,EMBEDDING_BACKEND=$EMBEDDING_BACKEND,EMBEDDING_LOCATION=$VERTEX_MODEL_LOCATION,MODEL_BACKEND=$MODEL_BACKEND,MODEL_LOCATION=$VERTEX_MODEL_LOCATION,$GENERATION_ENV,$ESTATE_ENV" \
  --quiet

SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo "==> Service URL: $SERVICE_URL (direct — not the public entry point, see below)"

# The org's `iam.allowedPolicyMemberDomains` policy refuses `allUsers` on a
# Cloud Run service, so `--allow-unauthenticated` above is best-effort and, on
# a project with that constraint, silently does not take: the direct URL 404s
# at Google's edge for every caller, including this deployment's own
# connector and the scheduler below. Firebase Hosting reaches Cloud Run
# through its own delegated identity, which the policy does not gate, so the
# Hosting domain — routed to this service by the rewrites in firebase.json —
# is the actual public entry point. Every self-referential URL below points
# there, never at the direct one.
HOSTING_URL="https://$PROJECT_ID.web.app"

echo "==> Updating PUBLIC_BASE_URL env var"
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "PUBLIC_BASE_URL=$HOSTING_URL" --quiet

# ---- Scheduling, sourced ----------------------------------------------------
#
# The connector pulls on a cadence, and nothing in Cumulus stays warm to make
# that happen: Scheduling is a capability filled by an adapter, the endpoint is
# the port, and Cloud Scheduler is the adapter here. The in-server loop that
# does this on a laptop is off in a deployment (ESTATE_PULL_INTERVAL_SECONDS=0).
#
# The two sweeps are on the same footing. Both are windowed Flows woken by a
# fact — "a window closed over the book" — and a scheduler is what closes a
# window when no source pushed anything.
if [[ -n "$ESTATE_PROJECT" ]]; then
  echo "==> Enabling Cloud Scheduler"
  gcloud services enable cloudscheduler.googleapis.com --quiet

  # These three calls go straight to the direct Cloud Run URL, not through
  # Hosting: a full estate pull or a sweep over the whole book can run past
  # Hosting's fixed ~60s rewrite-proxy timeout (measured: a cold pull 502'd at
  # exactly 60.09s), and that cap is not configurable. Cloud Run's own
  # timeout is, and is set to 600s above. Bypassing Hosting means bypassing
  # the org policy Hosting exists to route around, so the scheduler
  # authenticates as a specific principal instead: `run.invoker` for a
  # single named service account is a grant `iam.allowedPolicyMemberDomains`
  # allows (verified directly), unlike `allUsers`.
  echo "==> Granting run.invoker to $RUNTIME_SA (so the scheduler can call the service directly)"
  gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
    --member="serviceAccount:$RUNTIME_SA" --role=roles/run.invoker --quiet >/dev/null

  schedule_job() {
    local name="$1" cron="$2" path="$3" description="$4"
    # `create` and `update` take the same header pair under different flag
    # names (`--headers` vs. `--update-headers`) — the one place this command
    # is not symmetric, and worth a comment since it fails silently under
    # `set -e` otherwise: an unrecognized flag is a usage error, the job is
    # never touched, and nothing downstream notices until the job doesn't fire.
    local common_args=(
      --location="$REGION"
      --schedule="$cron"
      --time-zone="Etc/UTC"
      --uri="$SERVICE_URL$path"
      --http-method=POST
      --oidc-service-account-email="$RUNTIME_SA"
      --oidc-token-audience="$SERVICE_URL"
      --description="$description"
      --attempt-deadline=600s
      --quiet
    )
    if gcloud scheduler jobs describe "$name" --location="$REGION" >/dev/null 2>&1; then
      gcloud scheduler jobs update http "$name" "${common_args[@]}" \
        --update-headers="X-API-Key=$ADMIN_API_KEY" >/dev/null
      echo "    updated $name ($cron)"
    else
      gcloud scheduler jobs create http "$name" "${common_args[@]}" \
        --headers="X-API-Key=$ADMIN_API_KEY" >/dev/null
      echo "    created $name ($cron)"
    fi
  }

  echo "==> Scheduling the pull and the sweeps"
  schedule_job "cumulus-estate-pull" "*/15 * * * *" "/api/v1/estate/pull" \
    "Pull every registered estate feed's delta and deliver it through the door."
  schedule_job "cumulus-identity-sweep" "20 * * * *" "/api/v1/identity/sweep" \
    "Propose cross-source identity links over the counterparty book."
  schedule_job "cumulus-state-sweep" "40 * * * *" "/api/v1/state/sweep" \
    "Find disagreements between systems about the same commitment."
fi

echo "==> Configuring Flutter app for Firebase (web)"
cd "$REPO_ROOT/admin_app"
if ! command -v flutterfire >/dev/null; then
  dart pub global activate flutterfire_cli
  export PATH="$PATH:$HOME/.pub-cache/bin"
fi
flutterfire configure --project="$PROJECT_ID" --platforms=web --yes

echo "==> Building Flutter web app"
# Same-origin, deliberately: the browser cannot reach the direct Cloud Run URL
# either (see the HOSTING_URL note above), so the built app calls relative
# paths and firebase.json's rewrites carry them to the service from whichever
# origin actually served the page.
flutter build web --release \
  --dart-define=API_BASE_URL=""

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
echo "  Admin app:   $HOSTING_URL"
echo "  API + MCP:   $HOSTING_URL/api/v1   (MCP endpoint: $HOSTING_URL/mcp/)"
echo "               served through Firebase Hosting's rewrite in firebase.json —"
echo "               the direct Cloud Run URL ($SERVICE_URL) is not"
echo "               publicly invokable under this project's org policy."
if [[ -n "$ESTATE_PROJECT" ]]; then
  echo "  Estate:      $ESTATE_PROJECT / $ESTATE_NAMESPACE"
  echo "               writes $ESTATE_WRITES_ENABLED · pull every 15m · sweeps hourly"
else
  echo "  Estate:      none registered — the world comes up empty"
fi
echo "  World:       collection generation $COLLECTION_GENERATION"
echo
echo "Next: enable Email/Password sign-in (one-time, console UI):"
echo "  https://console.firebase.google.com/project/$PROJECT_ID/authentication/providers"
echo
echo "Then bootstrap your first owner:"
echo "  ./deploy/bootstrap_owner.sh you@example.com 'a-strong-password'"
echo "=========================================================="
