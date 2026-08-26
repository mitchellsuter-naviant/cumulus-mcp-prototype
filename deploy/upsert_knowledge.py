#!/usr/bin/env python3
"""Push `server/seed/` documents into a deployment that is already seeded.

`seed_if_empty` only runs against an empty corpus, so a new or edited governed
document never reaches a live deployment on its own. The live flows read their
governed values out of these documents at run time — the expense auto-approval
limit, the invoice auto-post limit, the PTO window — so a deploy that ships new
flow logic without its policy document ships a flow reading defaults.

Idempotent: POST, falling back to PUT on 409. Every PUT is a real revision bump
on the asset, which is exactly what a governed-knowledge edit should be.

    ./deploy/upsert_knowledge.py <SERVICE_URL> "$(cat deploy/.admin_api_key)"
    ./deploy/upsert_knowledge.py <SERVICE_URL> <KEY> pto-policy.md ...

With no filenames it pushes every document in `server/seed/`.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "server" / "seed"

# The fields an AssetCreate accepts; `revision` and the timestamps are the
# server's to assign.
FIELDS = [
    "id", "path", "title", "content", "content_type", "authority_level",
    "scope", "owner", "tags", "status",
]


def call(method: str, url: str, key: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": key},
        method=method,
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    base = sys.argv[1].rstrip("/")
    key = sys.argv[2]
    names = sys.argv[3:] or sorted(
        p.name for p in SEED.iterdir() if p.suffix in {".md", ".txt"}
    )

    # Imported late so `--help` works without the server package importable.
    sys.path.insert(0, str(REPO / "server"))
    from app.service import parse_seed_file

    failed = 0
    for name in names:
        asset = parse_seed_file(name, (SEED / name).read_text())
        payload = {field: getattr(asset, field) for field in FIELDS}
        status, body = call("POST", f"{base}/api/v1/assets", key, payload)
        if status == 409:
            asset_id = payload.pop("id")
            status, body = call(
                "PUT", f"{base}/api/v1/assets/{asset_id}", key, payload
            )
        if status not in (200, 201):
            failed += 1
            print(f"{name}: FAILED {status} {body.get('detail', body)}")
        else:
            print(f"{name}: {status} rev={body.get('revision', '?')}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
