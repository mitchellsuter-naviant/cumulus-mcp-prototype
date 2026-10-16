"""FastAPI app assembly: REST admin API + MCP (Streamable HTTP) + A2A."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import (
    a2a,
    api_keys_api,
    calendar_api,
    data_api,
    estate_api,
    evals_api,
    graph_api,
    ledger_api,
    mappings_api,
    modelcalls_api,
    identities_api,
    rest_api,
    runs_api,
    schedule_api,
    teams_api,
    ui_api,
    worlds_api,
    users_api,
    flows_api,
    tasks_api,
    reimbursements_api,
    employee_api,
    payables_api,
    queues_api,
)
from .config import settings
from .deps import (
    get_api_key_directory,
    get_calendar_service,
    get_asset_usage,
    get_anomaly_service,
    get_answer_service,
    get_dispatcher,
    get_document_service,
    get_estate_connector,
    get_graph_service,
    get_identity_service,
    get_run_engine,
    get_service,
    get_sync_service,
    get_queue_service,
    get_team_service,
    get_flow_service,
    get_task_service,
    get_reimbursement_service,
    get_ap_intake_service,
    get_credential_tracking_service,
    get_department_scheduling_service,
    get_extraction_service,
    get_field_documents_service,
    get_plant_eligibility_service,
    get_payable_service,
)
from .logging_config import RequestContextMiddleware, configure_logging, request_id
from .mcp_server import RequireAuthMiddleware, mcp

configure_logging(settings.firestore_project)
log = logging.getLogger("cumulus.main")

# Build the MCP ASGI app once; stateless JSON responses suit Cloud Run.
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/",
    json_response=True,
    stateless_http=True,
    host="0.0.0.0",  # disables localhost-only DNS-rebinding defaults for Cloud Run
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = get_service()  # hydrate storage + search index before serving
    get_identity_service()  # build the identity stores off the request path
    get_flow_service()  # hydrate the registry (and seed the demo fleet)
    get_task_service()  # hydrate the task queue (and seed the demo backlog)

    # The ontology and the Collection registry are declared in Python and
    # exported to the map, so they are republished on every start: if the
    # declaration changed, the document changes with it.
    try:
        from .platform_assets import publish_generated_assets

        publish_generated_assets(service)
    except Exception:
        log.exception("Publishing generated governed knowledge failed")

    # Every flow service is built here, not lazily, because a flow registers
    # its body with the run engine when it is constructed — and a Task decided
    # against a flow whose service was never built would have nothing to resume
    # into.
    get_graph_service()
    get_document_service()
    get_run_engine()
    # Before anything can run: the Dispatcher registers itself as the engine's
    # event sink when it is built, and a flow that ran first would emit into a
    # sink that did not exist yet.
    get_dispatcher()
    get_anomaly_service()
    get_sync_service()  # registers the four inbound sync bodies
    get_reimbursement_service()  # hydrate the table (and seed decided history)
    get_payable_service()  # hydrate the AP world (vendors, POs, invoices)
    # After payables, and that ordering is load-bearing: intake's whole output
    # is accounts-payable runs, and it must not be startable before the flow
    # it starts is registered.
    get_ap_intake_service()
    # Registers `credential-tracking`. Reaches no network and seeds
    # nothing; it is built here so a compliance window can be closed on a
    # cold box, and because the Trigger that closes one does not guard a
    # missing body.
    get_credential_tracking_service()
    # Registers `field-documents`, and this one is not optional on a cold
    # box: its two Triggers sit on `JobRecordsDelivered` and
    # `DocumentsDelivered`, both of which arrive through the delivery door,
    # and `Dispatcher.emit` starts every matched Trigger without guarding a
    # missing body — so an unregistered flow here would 500 the next delivery
    # for every feed rather than fail quietly in one run.
    get_field_documents_service()
    # The plant spine, in dependency order: the extraction registers first
    # because the sweep's whole output is extraction runs, and the sweep must
    # not be startable before the flow it starts is registered — `ap-intake`
    # and `accounts-payable` in the same order, for the same reason. Both are
    # built on a cold box because `plant-window-closed` is a timer and
    # `job-eligible` is emitted from inside the window it would fail.
    get_extraction_service()
    # And the day the extraction's demand rows are filled into, registered
    # before the sweep for the same reason: `department-day-opened` is a
    # declared Trigger and the sweep emits its fact from inside itself.
    get_department_scheduling_service()
    get_plant_eligibility_service()
    # Registers `answer-question`. Built here rather than on the first /ask so
    # a question arriving at the HR agent — which emits into the same flow and
    # holds no AnswerService of its own — cannot find the body missing.
    get_answer_service()
    # Last of the seeds: calendars hang off the employees the workforce seed
    # (which rides along with payables) has just landed.
    get_calendar_service()

    # Last, and after every seed: the connector delivers through the HTTP door,
    # so it must not start pulling into a half-built world. The loop is the dev
    # adapter for the Scheduling port and stays off unless an interval is set —
    # deployed, a managed scheduler POSTs /api/v1/estate/pull instead.
    try:
        get_estate_connector().start_interval_loop()
    except Exception:
        log.exception("Estate pull loop did not start")

    # Hard cutover to identity-bound credentials: a key minted before
    # identities existed authenticates nothing (auth.py refuses it), so mark
    # those keys revoked once at startup rather than leaving the console
    # reporting them as live. Idempotent — later instances find nothing to do.
    try:
        orphaned = get_api_key_directory().revoke_unattributed("system:identity-migration")
        if orphaned:
            log.warning(
                "Revoked API keys with no identity", extra={"count": orphaned}
            )
    except Exception:
        log.exception("Unattributed key revocation failed")

    # Teams own assets, and `Asset.owner` already carried bare slugs like
    # `platform-eng` before teams existed. Register those as real teams so the
    # references resolve; idempotent, so later instances find nothing to do.
    try:
        from .teams import bootstrap_teams_from_assets

        imported = bootstrap_teams_from_assets(
            get_team_service(), [a.owner for a in service.repo.all()]
        )
        if imported:
            log.info("Teams imported from asset ownership", extra={"count": imported})
    except Exception:
        log.exception("Team bootstrap failed")

    # Every queue the persona roster names gets a registry entry, so the desk
    # admin screen opens onto the desks that already exist rather than onto
    # nothing. Idempotent, and the roster keeps its authority either way —
    # `auth.queues_for` unions the two — so a failure here costs the console a
    # screen, never anybody their access.
    try:
        from .queues import bootstrap_from_roster

        created = bootstrap_from_roster(get_queue_service())
        if created:
            log.info("Queue registry bootstrapped", extra={"queuesCreated": created})
    except Exception:
        log.exception("Queue bootstrap failed")

    # Reconcile vectors against the corpus off the startup path: embedding a
    # large corpus can outlast the window a container has to report healthy,
    # and search works (lexically) throughout, sharpening as vectors land.
    service.warm_embeddings_async()

    log.info(
        "Startup complete",
        extra={
            "storageBackend": settings.storage_backend,
            "authBackend": settings.auth_backend,
            "userBackend": settings.user_backend,
            "embeddingBackend": settings.embedding_backend,
            "assets": len(service.repo.all()),
            "teams": len(get_team_service().list()),
            "flows": len(get_flow_service().list()),
            "openTasks": get_task_service().stats().open,
            "graphObjects": get_graph_service().stats()["objects"],
            "calendars": len(get_calendar_service().calendars()),
            "runs": len(get_run_engine().list()),
        },
    )
    # Mounted sub-app lifespans don't run automatically; drive the MCP session manager here.
    async with mcp.session_manager.run():
        yield
    # Buffered API-key usage is flushed opportunistically during requests; make
    # sure a graceful shutdown does not drop the tail.
    try:
        get_api_key_directory().flush()
        # Buffered usage would otherwise die with the instance.
        get_asset_usage().flush()
    except Exception:
        log.exception("Final usage flush failed")
    log.info("Shutdown complete")


app = FastAPI(title="Cumulus Knowledge Layer", version="0.1.0", lifespan=lifespan)

# Prototype-wide CORS so the Flutter web admin can call the REST API directly.
# Credentials travel in Authorization/X-API-Key headers rather than cookies, so
# a wildcard origin does not expose an ambient-credential CSRF surface — but
# narrow this to the Hosting origin once that URL is fixed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Outermost: every request (including the mounted MCP sub-app) gets an id and
# one structured access-log line.
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception(
        "Unhandled exception",
        extra={"method": request.method, "path": request.url.path},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id()},
    )


app.include_router(ui_api.router)
app.include_router(rest_api.router)
app.include_router(users_api.router)
app.include_router(identities_api.router)
app.include_router(teams_api.router)
app.include_router(flows_api.router)
app.include_router(tasks_api.router)
app.include_router(queues_api.router)
app.include_router(reimbursements_api.router)
app.include_router(payables_api.router)
app.include_router(employee_api.router)
app.include_router(calendar_api.router)
app.include_router(graph_api.router)
app.include_router(ledger_api.router)
app.include_router(mappings_api.router)
app.include_router(modelcalls_api.router)
app.include_router(runs_api.router)
app.include_router(data_api.router)
# The plant schedule's own door. Tier-1 rather than a console endpoint,
# because a customer pulling their own approved schedule with a script
# they wrote is a platform capability and not a screen's concern.
app.include_router(schedule_api.router)
app.include_router(estate_api.router)
app.include_router(evals_api.router)
app.include_router(worlds_api.router)
app.include_router(api_keys_api.router)
app.include_router(a2a.router)

# MCP Streamable HTTP endpoint at /mcp — credentials enforced at the transport.
app.mount("/mcp", RequireAuthMiddleware(mcp_app))


# Unauthenticated on purpose: Cloud Run and uptime probes need it. Reports only
# liveness and a document count, no document contents.
# Note: GFE intercepts /healthz on *.run.app, so /health is the public path.
@app.get("/health")
@app.get("/healthz")
def healthz() -> dict:
    service = get_service()
    return {
        "status": "ok",
        "assets": len(service.repo.all()),
        # Deliberately the summary, not the full status: this endpoint is
        # unauthenticated, and the model, dimensions, and fusion weights are
        # operator detail. `GET /api/v1/search/status` has the rest.
        "retrieval": service.retrieval_summary(),
    }
