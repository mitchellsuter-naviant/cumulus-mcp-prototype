#!/usr/bin/env bash
# Verify, build and deploy the whole stack in one command.
#
# `deploy/deploy.sh` remains the source of truth for *how* Cumulus deploys —
# APIs, Firestore, Cloud Run, FlutterFire, the web build, Hosting, the
# scheduler. This wraps it with the things a deploy of changed code needs and
# that script deliberately does not do:
#
#   1. runs the test suites and the analyzer BEFORE anything is pushed, so a
#      broken contract fails on this machine rather than in Cloud Run;
#   2. can push the governed knowledge documents, which `seed_if_empty` will
#      not update on a corpus that already has them;
#   3. can build and deploy the demo estate — a separate project with eight
#      hosted apps — which Cumulus reads from but does not contain.
#
# Nothing here re-implements deploy.sh. If a deployment step is wrong, it is
# wrong in deploy.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ESTATE_REPO_DEFAULT="$(cd "$REPO_ROOT/.." && pwd)/cumulus-demo-ecosystem"

# ---- defaults ---------------------------------------------------------------

REGION="${REGION:-us-central1}"
NAMESPACE="${ESTATE_NAMESPACE:-naviant}"
GENERATION="${COLLECTION_GENERATION:-}"
WRITES="${ESTATE_WRITES_ENABLED:-false}"

DO_CHECKS=true
DO_CUMULUS=true
DO_KNOWLEDGE=false
DO_ESTATE=false
ASSUME_YES=false
DRY_RUN=false

PROJECT=""
ESTATE=""
ESTATE_REPO="${ESTATE_REPO:-$ESTATE_REPO_DEFAULT}"

# ---- output -----------------------------------------------------------------

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
  GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; OFF=""
fi

say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==> %s%s\n' "$BOLD" "$*" "$OFF"; }
note() { printf '%s    %s%s\n' "$DIM" "$*" "$OFF"; }
warn() { printf '%s    %s%s\n' "$YELLOW" "$*" "$OFF"; }

die() {
  printf '\n%sFAILED:%s %s\n' "$RED" "$OFF" "$1" >&2
  shift || true
  for line in "$@"; do printf '    %s\n' "$line" >&2; done
  exit 1
}

usage() {
  cat <<'__HELP__'
ship.sh — verify, build and deploy the Cumulus stack.

USAGE
  ./deploy/ship.sh [options]

By default it runs the checks, then deploys Cumulus (server + admin app).
The knowledge push and the demo estate are opt-in, because each one changes
something a plain redeploy does not: a revision bump on governed documents,
and a second Google Cloud project.

STAGES
  checks      flutter analyze, flutter test, pytest      (skip: --skip-checks)
  cumulus     deploy/deploy.sh — Cloud Run + Hosting     (skip: --no-cumulus)
  knowledge   push server/seed/ documents                (enable: --knowledge)
  estate      build + deploy the demo ecosystem          (enable: --with-estate)

OPTIONS
  -p, --project ID      Cumulus GCP project    (default: .firebaserc)
  -r, --region REGION   Cloud Run region       (default: us-central1)
  -e, --estate ID       estate GCP project     (default: the estate repo's .firebaserc)
  -n, --namespace NS    estate namespace       (default: naviant)
      --generation Vn   collection generation  (default: deploy.sh's own)
      --writes          allow writes back into the customer's systems
      --estate-repo DIR path to cumulus-demo-ecosystem

      --skip-checks     deploy without running the suites
      --checks-only     run the suites and stop
      --no-cumulus      skip the Cumulus deploy (for a knowledge- or estate-only run)
      --knowledge       push server/seed/ documents after deploying
      --with-estate     also build and deploy the demo ecosystem

  -y, --yes             do not ask before deploying
      --dry-run         print what would run, touch nothing
  -h, --help            this

EXAMPLES
  ./deploy/ship.sh                          # check, then deploy Cumulus
  ./deploy/ship.sh --checks-only            # just the suites
  ./deploy/ship.sh --knowledge -y           # deploy + push seed docs, no prompt
  ./deploy/ship.sh --with-estate            # deploy Cumulus and the estate
  ./deploy/ship.sh --no-cumulus --knowledge # push seed docs only
__HELP__
}

# ---- arguments --------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--project)     PROJECT="${2:?--project needs a value}"; shift 2 ;;
    -r|--region)      REGION="${2:?--region needs a value}"; shift 2 ;;
    -e|--estate)      ESTATE="${2:?--estate needs a value}"; shift 2 ;;
    -n|--namespace)   NAMESPACE="${2:?--namespace needs a value}"; shift 2 ;;
    --generation)     GENERATION="${2:?--generation needs a value}"; shift 2 ;;
    --estate-repo)    ESTATE_REPO="${2:?--estate-repo needs a value}"; shift 2 ;;
    --writes)         WRITES=true; shift ;;
    --skip-checks)    DO_CHECKS=false; shift ;;
    --checks-only)    DO_CUMULUS=false; DO_KNOWLEDGE=false; DO_ESTATE=false
                      CHECKS_ONLY=true; shift ;;
    --no-cumulus)     DO_CUMULUS=false; shift ;;
    --knowledge)      DO_KNOWLEDGE=true; shift ;;
    --with-estate)    DO_ESTATE=true; shift ;;
    -y|--yes)         ASSUME_YES=true; shift ;;
    --dry-run)        DRY_RUN=true; shift ;;
    -h|--help)        usage; exit 0 ;;
    *)                die "Unknown option: $1" "Run ./deploy/ship.sh --help" ;;
  esac
done
CHECKS_ONLY="${CHECKS_ONLY:-false}"

# `--checks-only --skip-checks` asks for nothing at all. Say so rather than
# exiting 0 as though something had been done.
if [[ "$CHECKS_ONLY" == true && "$DO_CHECKS" == false ]]; then
  die "--checks-only and --skip-checks cancel each other out; nothing would run."
fi
if [[ "$DO_CHECKS" == false && "$DO_CUMULUS" == false \
   && "$DO_KNOWLEDGE" == false && "$DO_ESTATE" == false ]]; then
  die "Every stage is disabled; nothing would run." "Run ./deploy/ship.sh --help"
fi

# ---- resolving what we are pointed at ---------------------------------------

# The project a repo is bound to already lives in its `.firebaserc`, and
# `bootstrap_owner.sh` reads it from exactly there. Asking the operator to
# retype it is how a deploy ends up in the wrong project.
firebaserc_project() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  python3 -c "
import json, sys
try:
    print(json.load(open('$file'))['projects']['default'])
except Exception:
    sys.exit(1)
" 2>/dev/null
}

if [[ -z "$PROJECT" ]]; then
  PROJECT="$(firebaserc_project "$REPO_ROOT/.firebaserc" || true)"
fi
if [[ -z "$PROJECT" && ( "$DO_CUMULUS" == true || "$DO_KNOWLEDGE" == true ) ]]; then
  die "No Cumulus project." \
      "Pass one:            ./deploy/ship.sh --project <PROJECT_ID>" \
      "or bind the repo:    echo '{\"projects\":{\"default\":\"<ID>\"}}' > .firebaserc"
fi

# The estate project is per-developer and deliberately has no baked-in default
# — deploy.sh refuses to guess one for the same reason. Reading it from the
# estate repo's own binding is not a guess.
if [[ -z "$ESTATE" ]]; then
  ESTATE="${ESTATE_PROJECT:-}"
fi
if [[ -z "$ESTATE" && -d "$ESTATE_REPO" ]]; then
  ESTATE="$(firebaserc_project "$ESTATE_REPO/.firebaserc" || true)"
fi

HOSTING_URL="https://$PROJECT.web.app"
KEY_FILE="$REPO_ROOT/deploy/.admin_api_key"

# ---- preflight --------------------------------------------------------------

need() {
  command -v "$1" >/dev/null 2>&1 || die "\`$1\` is not on PATH." "${2:-}"
}

step "Preflight"

if [[ "$DO_CHECKS" == true ]]; then
  need flutter "Install the Flutter SDK, or pass --skip-checks."
  SERVER_PY="$REPO_ROOT/server/.venv/bin/python"
  if [[ ! -x "$SERVER_PY" ]]; then
    die "No server virtualenv at server/.venv." \
        "Create it:" \
        "  cd server && python3 -m venv .venv" \
        "  .venv/bin/pip install -r requirements.txt" \
        "Or pass --skip-checks."
  fi
fi

if [[ "$DO_CUMULUS" == true ]]; then
  need gcloud   "Install the Google Cloud SDK: https://cloud.google.com/sdk"
  need firebase "Install the Firebase CLI: npm i -g firebase-tools"
  need flutter  "deploy.sh builds the web app."
  [[ -x "$REPO_ROOT/deploy/deploy.sh" ]] \
    || die "deploy/deploy.sh is missing or not executable."
fi

if [[ "$DO_KNOWLEDGE" == true ]]; then
  need python3
  [[ -f "$KEY_FILE" ]] || die \
    "deploy/.admin_api_key does not exist yet." \
    "It is generated by the first run of deploy/deploy.sh, so deploy once" \
    "before pushing knowledge (drop --no-cumulus)."
fi

if [[ "$DO_ESTATE" == true ]]; then
  need firebase
  need flutter
  [[ -d "$ESTATE_REPO" ]] || die \
    "No estate repo at $ESTATE_REPO." \
    "Point at it with --estate-repo <DIR>."
  [[ -n "$ESTATE" ]] || die \
    "No estate project." \
    "Pass one with --estate <PROJECT_ID>."
fi
note "ok"

# ---- the plan ---------------------------------------------------------------

plan_line() { printf '    %-11s %s\n' "$1" "$2"; }

step "Plan"
[[ "$DO_CHECKS"    == true ]] && plan_line "checks"    "analyze + flutter test + pytest"
[[ "$DO_CUMULUS"   == true ]] && plan_line "cumulus"   "$PROJECT ($REGION) -> $HOSTING_URL"
[[ "$DO_KNOWLEDGE" == true ]] && plan_line "knowledge" "server/seed/ -> $HOSTING_URL (bumps revisions)"
[[ "$DO_ESTATE"    == true ]] && plan_line "estate"    "$ESTATE — 8 apps + functions"
if [[ "$DO_CUMULUS" == true ]]; then
  if [[ -n "$ESTATE" ]]; then
    plan_line "" "estate read: $ESTATE / $NAMESPACE (writes: $WRITES)"
  else
    warn "No estate registered — Cumulus will deploy with an empty world."
  fi
  [[ -n "$GENERATION" ]] && plan_line "" "collection generation: $GENERATION"
fi
$DRY_RUN && note "dry run — nothing will be executed"

# Deploying is outward-facing and hard to take back, so it is confirmed once,
# here, before any of it starts. The checks are not: they touch nothing.
if [[ "$DRY_RUN" == false && "$ASSUME_YES" == false ]] \
   && [[ "$DO_CUMULUS" == true || "$DO_KNOWLEDGE" == true || "$DO_ESTATE" == true ]]; then
  printf '\n%sDeploy this?%s [y/N] ' "$BOLD" "$OFF"
  read -r reply </dev/tty || reply=""
  [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]] || die "Cancelled."
fi

# ---- stages -----------------------------------------------------------------

STARTED_AT=$SECONDS
SUMMARY=()

record() { SUMMARY+=("$1"); }

# Run a command, or describe it under --dry-run. Deliberately not `eval`: the
# arguments arrive as an array and stay one.
run() {
  if $DRY_RUN; then
    printf '%s    [dry-run] %s%s\n' "$DIM" "$*" "$OFF"
    return 0
  fi
  "$@"
}

stage_checks() {
  step "Checks"
  local t0=$SECONDS

  note "flutter analyze"
  run bash -c "cd '$REPO_ROOT/admin_app' && flutter analyze"

  note "flutter test"
  run bash -c "cd '$REPO_ROOT/admin_app' && flutter test"

  note "pytest"
  run bash -c "cd '$REPO_ROOT/server' && .venv/bin/python -m pytest -q"

  record "checks     $((SECONDS - t0))s"
}

stage_cumulus() {
  step "Deploying Cumulus"
  local t0=$SECONDS

  # deploy.sh reads its estate settings from the environment and takes the
  # project and region as positional arguments. Passing the generation only
  # when one was asked for leaves deploy.sh's own default in charge, which is
  # where the pin belongs.
  local env=(
    "ESTATE_PROJECT=$ESTATE"
    "ESTATE_NAMESPACE=$NAMESPACE"
    "ESTATE_WRITES_ENABLED=$WRITES"
  )
  [[ -n "$GENERATION" ]] && env+=("COLLECTION_GENERATION=$GENERATION")

  run env "${env[@]}" "$REPO_ROOT/deploy/deploy.sh" "$PROJECT" "$REGION"
  record "cumulus    $((SECONDS - t0))s"
}

stage_knowledge() {
  step "Pushing governed knowledge"
  local t0=$SECONDS
  note "each changed document becomes a new revision"

  # Through Hosting, not the direct Cloud Run URL: that one is not publicly
  # invokable under this project's org policy, which is why deploy.sh and
  # bootstrap_owner.sh both address the service this way too.
  #
  # Not routed through `run`: that echoes its arguments under --dry-run, and
  # one of these is the admin key. A dry run is the thing you paste into a
  # chat window to ask whether it looks right.
  if $DRY_RUN; then
    printf '%s    [dry-run] %s %s <admin key from deploy/.admin_api_key>%s\n' \
      "$DIM" "deploy/upsert_knowledge.py" "$HOSTING_URL" "$OFF"
  else
    "$REPO_ROOT/deploy/upsert_knowledge.py" "$HOSTING_URL" "$(cat "$KEY_FILE")"
  fi
  record "knowledge  $((SECONDS - t0))s"
}

stage_estate() {
  step "Deploying the demo estate"
  local t0=$SECONDS

  # melos drives the workspace build. It is usually a pub global package
  # rather than something on PATH, so resolve it rather than assuming.
  local melos="melos"
  if ! command -v melos >/dev/null 2>&1; then
    if [[ -x "$HOME/.pub-cache/bin/melos" ]]; then
      melos="$HOME/.pub-cache/bin/melos"
    else
      note "activating melos"
      run dart pub global activate melos
      melos="$HOME/.pub-cache/bin/melos"
    fi
  fi

  note "resolving the workspace"
  run bash -c "cd '$ESTATE_REPO' && dart pub get"

  note "building eight web apps (slow)"
  run bash -c "cd '$ESTATE_REPO' && '$melos' run build:web"

  note "deploying hosting + functions"
  run bash -c "cd '$ESTATE_REPO' && firebase deploy --project '$ESTATE'"

  record "estate     $((SECONDS - t0))s"
}

$DO_CHECKS    && stage_checks
$DO_CUMULUS   && stage_cumulus
$DO_KNOWLEDGE && stage_knowledge
$DO_ESTATE    && stage_estate

# ---- summary ----------------------------------------------------------------

step "Done in $((SECONDS - STARTED_AT))s"
for line in "${SUMMARY[@]}"; do note "$line"; done

if $DRY_RUN; then
  note "dry run — nothing was deployed"
  exit 0
fi

if $DO_CUMULUS; then
  printf '\n'
  say "  Admin app:  $HOSTING_URL"
  say "  API + MCP:  $HOSTING_URL/api/v1   (MCP: $HOSTING_URL/mcp/)"
  say "  Full log:   deploy/last-deploy.log"
  if [[ -n "$ESTATE" ]]; then
    say "  Estate:     $ESTATE / $NAMESPACE (writes: $WRITES)"
  fi
  printf '\n'
  say "  First deploy of a project also needs, once:"
  say "    Email/Password sign-in — https://console.firebase.google.com/project/$PROJECT/authentication/providers"
  say "    ./deploy/bootstrap_owner.sh you@example.com 'a-strong-password'"
fi
if $DO_ESTATE; then
  say "  Estate apps: https://$ESTATE-dms.web.app (and crm, erp, hris, admin, office, pas, psa)"
fi

# A checks-only run deployed nothing, and saying "shipped" would be a lie in
# the one word the operator actually reads.
if [[ "$DO_CUMULUS" == false && "$DO_KNOWLEDGE" == false && "$DO_ESTATE" == false ]]; then
  printf '\n%sChecks passed. Nothing was deployed.%s\n' "$GREEN" "$OFF"
else
  printf '\n%sShipped.%s\n' "$GREEN" "$OFF"
fi
