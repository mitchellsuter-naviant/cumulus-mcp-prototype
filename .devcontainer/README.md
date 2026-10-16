# Cumulus MCP Prototype dev container

One reproducible Linux environment for both halves of the prototype, plus the
simulation engine they run against and the from-scratch rebuild this
prototype guides:

- **Flutter web admin app** (`admin_app/`) — Firebase Auth + Firebase Hosting
- **Python API server** (`server/`) — FastAPI serving three front doors over the
  same knowledge store: REST (`/api/v1`), MCP over Streamable HTTP (`/mcp/`), and
  A2A (`/a2a`). Storage is Firestore in production, in-memory locally. Hosted on
  **Cloud Run** in GCP project `cumulus-mcp-prototype`.
- **Simulation engine** (`cumulus-demo-ecosystem/`) — a *separate repo*,
  bind-mounted in and opened alongside this one. See
  [Three trees, one window](#three-trees-one-window).
- **Cumulus proper** (`cumulus-vision/`) — a *separate repo*, rebuilt one
  step at a time with this prototype as the guide (FastAPI server in
  `server/` on port 8081 locally, Flutter web console in `console/`, deployed
  to GCP project `cumulus-vision-507711`), bind-mounted in the same way. See
  [Three trees, one window](#three-trees-one-window).
- **customer-ai-projects/** — another project directory, bind-mounted
  read-only purely for reference (not built, run, or deployed from here).

## What's inside

| Tool               | Notes                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------- |
| Conda (Miniforge)  | Base interpreter (3.13). Server deps live in `server/.venv`, built on container create and auto-activated in terminals |
| Flutter / Dart SDK | Pinned to `3.35.7` via `FLUTTER_VERSION` in the `Dockerfile`; web artifacts only      |
| Chromium           | `CHROME_EXECUTABLE` is set, for `flutter test --platform chrome`                      |
| Firebase CLI       | `flutterfire configure`, `firebase deploy --only hosting`                             |
| FlutterFire CLI    | Activated in `~/.pub-cache/bin`                                                       |
| Google Cloud CLI   | `gcloud run deploy`; project preset via `CLOUDSDK_CORE_PROJECT=cumulus-mcp-prototype` |
| GitHub CLI         | `gh`, via a dev container feature                                                     |

Runs as root (the Miniforge image's standard pattern).

## Getting started

1. Host prerequisites: Docker Desktop + the VS Code
   [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
   extension.
2. Clone the simulation engine, create the vision directory, and make sure
   `customer-ai-projects/` is present, **beside** this repo, so all four are
   siblings:

   ```sh
   cd ..            # the directory containing cumulus-mcp-prototype/
   git clone <cumulus-demo-ecosystem remote> cumulus-demo-ecosystem
   mkdir -p cumulus-vision   # or clone it, if it already has a remote
   ```

   This is a hard prerequisite: all three mounts are binds, and Docker
   refuses to start a container whose bind source does not exist.
3. Authenticate gcloud on the **host** (the config dir is bind-mounted in):

   ```sh
   gcloud auth login
   gcloud auth application-default login
   ```

4. Load your SSH signing key into the host agent _before_ opening the container
   so commits get signed (see `setup-git-signing.sh` for the per-OS command).
5. **Dev Containers: Reopen in Container**. It opens the multi-root workspace
   `cumulus.code-workspace`, not the bare folder. On first create,
   `postCreateCommand` runs `flutter pub get` in `admin_app/` and builds
   `server/.venv` from `server/requirements.txt` (each step skipped if that half
   of the repo is absent).

## Three trees, one window

`cumulus-demo-ecosystem/` — the simulation engine this prototype codes against —
and `cumulus-vision/` — Cumulus proper, rebuilt one step at a time — are each a
separate directory with its own history/remote (once `cumulus-vision` has one).
Both are **bind-mounted, not copied**: the container sees the host checkout, so
edits, branches, and commits made in either place are the same working tree.
`customer-ai-projects/` rides along the same way but **read-only** — it's
there purely for reference, not edited or run from this workspace.

| Where | cumulus-demo-ecosystem | cumulus-vision | customer-ai-projects |
| ----- | ---------------------- | --------------- | --------------------- |
| Host | `<parent of this repo>/cumulus-demo-ecosystem` | `<parent of this repo>/cumulus-vision` | `<parent of this repo>/customer-ai-projects` |
| Container | `/workspaces/cumulus-demo-ecosystem` | `/workspaces/cumulus-vision` | `/workspaces/customer-ai-projects` (read-only) |

Two pieces of config make that work, and they must agree:

- `devcontainer.json` → `mounts` binds
  `${localWorkspaceFolder}/../cumulus-demo-ecosystem` to
  `/workspaces/cumulus-demo-ecosystem` (and likewise for `cumulus-vision` and
  the read-only `customer-ai-projects`), and `workspaceFolder` points at
  `cumulus.code-workspace` so VS Code attaches to the multi-root workspace.
- [`cumulus.code-workspace`](../cumulus.code-workspace) (repo root) lists all
  four folders and carries the settings that need a folder qualifier — in a
  multi-root window a bare `${workspaceFolder}` is ambiguous, so the Python
  interpreter and pytest paths are written
  `${workspaceFolder:cumulus-mcp-prototype}/…`.

Consequences worth knowing:

- **Cloned somewhere else?** Change the `source=` of that mount in
  `devcontainer.json` and the matching `path` in `cumulus.code-workspace`, then
  rebuild.
- **Git is per-folder.** The Source Control view shows one repository per
  folder that has one. Each is registered as `safe.directory` by
  `postCreateCommand`. Commit signing applies to all of them — it is
  configured globally.
- **Dependency setup is per tree.** `postCreateCommand` bootstraps this repo
  and, with the same existence guard, `cumulus-vision/server/.venv`; the
  simulation engine sets itself up per its own README. If a tree grows a
  build the container needs on every create, add it there rather than
  assuming it.
- **Deploy scripts stay single-repo.** `deploy/deploy.sh` still operates on
  `cumulus-mcp-prototype` only.
- **Claude Code reaches all three mounts.** `.claude/settings.json` lists
  `/workspaces/cumulus-demo-ecosystem`, `/workspaces/cumulus-vision`, and
  `/workspaces/customer-ai-projects` under `permissions.additionalDirectories`,
  so a session started in this repo can read all three without a prompt per
  file (writes to `customer-ai-projects` fail regardless — the mount itself
  is read-only).

Prefer running the container's Dockerfile over adding engine toolchains ad hoc: if
the engine needs a runtime this image lacks, add it to
[`Dockerfile`](Dockerfile) so the environment stays reproducible.

## Running the front end

The container has no display, so `-d chrome` can't open a window. Use the web
server device and the browser on your host:

```sh
cd admin_app
flutter run -d web-server --web-port 3000 --web-hostname 0.0.0.0 \
  --dart-define=API_BASE_URL=http://localhost:8080
```

Then open <http://localhost:3000> (port 3000 is forwarded). Pressing **Run**
in VS Code does this for you — `dart.flutterRunAdditionalArgs` is preconfigured.

## Running the API server

`server/.venv` is the configured interpreter. Run it on port 8080 (forwarded;
the same port Cloud Run injects via `$PORT`):

```sh
cd server
.venv/bin/pip install -r requirements.txt   # after changing dependencies
.venv/bin/uvicorn app.main:app --port 8080 --reload
```

Defaults to the in-memory backend with `AUTH_BACKEND=none`, auto-seeded from
`server/seed/` — so every caller is treated as an owner and no credential is
needed locally. Health check: `curl localhost:8080/health`.

Logs are JSON on stdout; pipe through `jq` for readability:

```sh
.venv/bin/uvicorn app.main:app --port 8080 --reload 2>&1 | jq -c .
```

Tests must run with `server/` as the working directory:

```sh
cd server && .venv/bin/python -m pytest tests/
```

MCP clients (Claude Desktop, VS Code, etc.) connect to `http://localhost:8080/mcp/`
locally, or the deployed Cloud Run URL's `/mcp/` path. The deployed endpoint
requires a credential — see [Connect an MCP client](../README.md#connect-an-mcp-client).

### Deploying

The service is named `cumulus-knowledge`. Use the repo's deploy script rather
than a hand-rolled `gcloud run deploy` — it also handles Firestore, the generated
`ADMIN_API_KEY`, FlutterFire config, and Firebase Hosting:

```sh
./deploy/deploy.sh cumulus-mcp-prototype
```

See the [root README](../README.md#deploy) for the server-only redeploy command.

## Firebase

```sh
firebase login --no-localhost          # browser-based OAuth won't loop back into the container
cd admin_app
flutterfire configure --platforms=web  # regenerates lib/firebase_options.dart
flutter build web --dart-define=API_BASE_URL=<SERVICE_URL>
cd .. && firebase deploy --only hosting   # firebase.json serves admin_app/build/web
```

Credentials persist in the `cumulus-mcp-prototype-configstore` volume, so you log in once
per machine rather than once per rebuild.

## Persisted state

Named volumes survive rebuilds (`docker volume rm` to reset):

- `cumulus-mcp-prototype-claude` — Claude Code sessions, memory, and login
- `cumulus-mcp-prototype-configstore` — Firebase CLI credentials
- `cumulus-mcp-prototype-pub-cache` — downloaded pub packages

The host's `~/.config/gcloud` is bind-mounted, so gcloud/ADC credentials are
shared with the host rather than stored in the container.

## Upgrading toolchains

- **Flutter**: bump `ARG FLUTTER_VERSION` in the `Dockerfile` to a
  [Flutter release tag](https://github.com/flutter/flutter/releases), keep it
  in step with the `sdk:` constraint in `pubspec.yaml`.
- **Python**: the interpreter comes from the Miniforge base image. To move it,
  pin a different `condaforge/miniforge3` tag in the `Dockerfile` and keep
  `server/Dockerfile`'s `FROM python:` tag in step, so the dev and Cloud Run
  runtimes match. Delete `server/.venv` and rebuild to pick up the change.

Then **Dev Containers: Rebuild Container**.
