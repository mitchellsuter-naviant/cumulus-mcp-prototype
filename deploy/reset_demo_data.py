#!/usr/bin/env python3
"""Delete the generated demo collections so the service reseeds them clean.

Everything this touches is seed or demo data that `seed_*_if_empty` rebuilds
on the next cold start. It never touches the collections that hold authored
state: `assets`, `teams`, `users`, `api_keys`, `identities`, or the usage and
embedding rollups.

Two reasons to run it. One: the collection names drifted. Renames left three
generations behind — `workflows*` from before the Workflow/DataFlow retirement,
`flows*`/`tasks` from the six-definition fleet, and a `_v2` set added as env
overrides to shadow the stale seeds — and reading the canonical names again
means clearing all of them first. Two: resetting a demo, where live
submissions have left the tables in a mid-flight state.

    ./deploy/reset_demo_data.py [PROJECT_ID]            # dry run, prints counts
    ./deploy/reset_demo_data.py [PROJECT_ID] --apply

ORDER MATTERS if you are undoing the `_v2` overrides. Purge first, then drop
the env vars:

    gcloud run services update cumulus-knowledge --region us-central1 \\
      --remove-env-vars FLOWS_COLLECTION,GOLDEN_SETS_COLLECTION,\\
    TEST_RUNS_COLLECTION,TASKS_COLLECTION

Dropping them first would point the service at the *stale* collections and
serve the retired six-definition fleet. The update itself creates a new
revision, which cold-starts and reseeds.
"""

import sys

TARGETS = [
    # Pre-rename generation.
    "workflows",
    "workflow_golden_sets",
    # Pre-strip generation: the retired six-definition fleet.
    "flows",
    "flow_golden_sets",
    "flow_test_runs",
    "tasks",
    # The shadow set, redundant once the env overrides are gone.
    "flows_v2",
    "flow_golden_sets_v2",
    "flow_test_runs_v2",
    "tasks_v2",
    # Reseeded so the demo is internally consistent: a live submission leaves
    # a pending row pointing at a Task, and clearing tasks without clearing
    # these would leave that pointer dangling.
    "payables",
    "reimbursements",
    # The generation the flow rewrite reshaped, pinned by deploy.sh.
    "tasks_v3",
    "reimbursements_v3",
    "payables_v3",
    # The entity layer, the run journals, the artifacts, and the derived
    # profile. These have to go *with* the tables above rather than instead of
    # them: the rows are a projection of the runs, the runs reference the
    # objects, and the objects are what the anomaly profile is computed from.
    # Clearing one and not the others is worse than clearing none — it leaves a
    # graph full of invoices no row or run accounts for.
    "graph_objects",
    "graph_links",
    "flow_runs",
    "documents",
    "vendor_profiles",
]

# Named so a reader knows the omission is deliberate rather than an oversight.
PRESERVED = [
    # `assets` above all: the corpus is authored, and it is also where
    # `upsert_knowledge.py` puts governed documents that a reseed must read.
    # Wiping it would make the reseed build a world against default values.
    "assets", "asset_embeddings", "asset_usage", "teams", "users",
    "api_keys", "api_key_usage", "identities",
    "employee_profiles", "employee_pto",
]


def main() -> int:
    from google.cloud import firestore

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    project = args[0] if args else "cumulus-mcp-prototype"
    apply = "--apply" in sys.argv

    client = firestore.Client(project=project)
    total = 0
    for name in TARGETS:
        collection = client.collection(name)
        docs = list(collection.limit(1000).stream())
        if not docs:
            print(f"{name:26} empty")
            continue
        total += len(docs)
        if not apply:
            print(f"{name:26} would delete {len(docs)}")
            continue
        batch = client.batch()
        for i, doc in enumerate(docs, 1):
            batch.delete(doc.reference)
            if i % 400 == 0:  # Firestore caps a batch at 500 writes.
                batch.commit()
                batch = client.batch()
        batch.commit()
        print(f"{name:26} deleted {len(docs)}")

    print(f"\npreserved: {', '.join(PRESERVED)}")
    if apply:
        print(f"\nDeleted {total} documents. Restart the service to reseed:")
        print("  gcloud run services update cumulus-knowledge "
              "--region us-central1 --update-env-vars RESEED_AT=$(date +%s)")
    else:
        print(f"\nDRY RUN — {total} documents. Pass --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
