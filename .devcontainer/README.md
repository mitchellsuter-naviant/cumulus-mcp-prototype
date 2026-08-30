# Cumulus MCP Prototype dev container

One reproducible Linux environment for both halves of the prototype, plus the
simulation engine they run against:

- **Flutter web admin app** (`admin_app/`) — Firebase Auth + Firebase Hosting
- **Python API server** (`server/`) — FastAPI serving three front doors over the
  same knowledge store: REST (`/api/v1`), MCP over Streamable HTTP (`/mcp/`), and
  A2A (`/a2a`). Storage is Firestore in production, in-memory locally. Hosted on
  **Cloud Run** in GCP project `cumulus-mcp-prototype`.
- **Simulation engine** (`cumulus-demo-ecosystem/`) — a *separate repo*,
  bind-mounted in and opened alongside this one. See
  [Two repos, one window](#two-repos-one-window).

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
2. Clone the simulation engine **beside** this repo, so the two are siblings:

   ```sh
   cd ..            # the directory containing cumulus-mcp-prototype/
   git clone <cumulus-demo-ecosystem remote> cumulus-demo-ecosystem
   ```

   This is a hard prerequisite: the mount is a bind, and Docker refuses to start
   a container whose bind source does not exist.
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

## Two repos, one window

`cumulus-demo-ecosystem/` — the simulation engine this prototype codes against —
is a separate repo with its own history and its own remote. It is **bind-mounted,
not copied**: the container sees the host checkout, so edits, branches, and
commits made in either place are the same working tree.

| Where | Path |
| ----- | ---- |
| Host | `<parent of this repo>/cumulus-demo-ecosystem` |
| Container | `/workspaces/cumulus-demo-ecosystem` |

Two pieces of config make that work, and they must agree:

- `devcontainer.json` → `mounts` binds
  `${localWorkspaceFolder}/../cumulus-demo-ecosystem` to
  `/workspaces/cumulus-demo-ecosystem`, and `workspaceFolder` points at
  `cumulus.code-workspace` so VS Code attaches to the multi-root workspace.
- [`cumulus.code-workspace`](../cumulus.code-workspace) (repo root) lists both
  folders and carries the settings that need a folder qualifier — in a multi-root
  window a bare `${workspaceFolder}` is ambiguous, so the Python interpreter and
  pytest paths are written `${workspaceFolder:cumulus-mcp-prototype}/…`.

Consequences worth knowing:

- **Cloned somewhere else?** Change the `source=` of that mount in
  `devcontainer.json` and the second `path` in `cumulus.code-workspace`, then
  rebuild.
- **Git is per-folder.** The Source Control view shows two repositories. Both are
  registered as `safe.directory` by `postCreateCommand`. Commit signing applies to
  both — it is configured globally.
- **No shared dependency install.** `postCreateCommand` bootstraps only this repo;
  the simulation engine sets itself up per its own README. If it grows a build the
  container needs on every create, add it there rather than assuming it.
- **Deploy scripts stay single-repo.** `deploy/deploy.sh` still operates on
  `cumulus-mcp-prototype` only.
- **Claude Code reaches both.** `.claude/settings.json` lists
  `/workspaces/cumulus-demo-ecosystem` under `permissions.additionalDirectories`,
  so a session started in this repo can read and edit the engine without a prompt
  per file.

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
