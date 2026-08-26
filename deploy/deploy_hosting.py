#!/usr/bin/env python3
"""Deploy `admin_app/build/web` to Firebase Hosting over the REST API.

Why this exists rather than `firebase deploy --only hosting`: the firebase CLI
needs its own interactive login, which a dev container or CI runner does not
have, and it refuses Application Default Credentials without a quota project.
The REST API accepts the gcloud user token plus an explicit
`x-goog-user-project` header, which is the documented way through. Anyone with
`gcloud auth login` can therefore deploy with no extra credential.

    cd admin_app && flutter build web --release --dart-define=API_BASE_URL=<URL>
    ./deploy/deploy_hosting.py [PROJECT_ID]

The flow is the one the CLI runs: create a version, declare every file's hash,
upload only what the API reports missing, finalize, release.
"""

import gzip
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "admin_app" / "build" / "web"
API = "https://firebasehosting.googleapis.com/v1beta1"


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else "cumulus-mcp-prototype"
    # The Hosting site id matches the project id for the default site.
    site = project

    if not (WEB / "index.html").exists():
        print(f"No build at {WEB} — run `flutter build web --release` first.")
        return 2

    token = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "x-goog-user-project": project,
        "Content-Type": "application/json",
    }

    def call(method, url, payload=None, raw=None, override=None):
        data = raw if raw is not None else (
            json.dumps(payload).encode() if payload is not None else None
        )
        request = urllib.request.Request(
            url, data=data, headers=override or headers, method=method
        )
        with urllib.request.urlopen(request) as response:
            body = response.read()
            return json.loads(body) if body else {}

    # The SPA rewrite lives in firebase.json; carry it onto the version so a
    # deep link does not 404.
    hosting = json.loads((REPO / "firebase.json").read_text())["hosting"]
    version = call("POST", f"{API}/sites/{site}/versions", {
        "config": {
            "rewrites": [
                {"glob": r["source"], "path": r["destination"]}
                for r in hosting.get("rewrites", [])
            ]
        }
    })
    name = version["name"]
    print(f"version: {name}")

    # Hashes are of the gzipped bytes, with a fixed mtime so an unchanged file
    # hashes the same on every run and is not re-uploaded.
    files: dict[str, str] = {}
    blobs: dict[str, bytes] = {}
    for path in WEB.rglob("*"):
        if not path.is_file():
            continue
        compressed = gzip.compress(path.read_bytes(), mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        files["/" + str(path.relative_to(WEB))] = digest
        blobs[digest] = compressed

    populated = call("POST", f"{API}/{name}:populateFiles", {"files": files})
    required = populated.get("uploadRequiredHashes") or []
    upload_url = populated["uploadUrl"]
    print(f"files: {len(files)} · to upload: {len(required)}")

    for i, digest in enumerate(required, 1):
        call(
            "POST",
            f"{upload_url}/{digest}",
            raw=blobs[digest],
            override={
                "Authorization": f"Bearer {token}",
                "x-goog-user-project": project,
                "Content-Type": "application/octet-stream",
            },
        )
        if i % 10 == 0 or i == len(required):
            print(f"uploaded {i}/{len(required)}")

    call("PATCH", f"{API}/{name}?update_mask=status", {"status": "FINALIZED"})
    release = call("POST", f"{API}/sites/{site}/releases?versionName={name}", {})
    print(f"released: {release.get('name', '?')}")
    print(f"live: https://{site}.web.app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
