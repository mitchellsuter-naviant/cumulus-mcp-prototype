"""Authentication and role-based authorization.

AUTH_BACKEND=none (default): every request acts as a dev owner — local development.
AUTH_BACKEND=firebase: requests must carry a Firebase ID token as `Authorization:
Bearer <token>`; the user's role lives in the `role` custom claim.

Machine clients (MCP configurations, integrations) present X-API-Key. Two kinds
are accepted, checked in this order:
  1. ADMIN_API_KEY  — the legacy single env-var credential, owner privileges.
  2. Minted keys    — `cumk_<id>_<secret>`, per-client, revocable, capped at
                      admin. See api_keys.py. Every request they make is
                      attributed to the key id and counted.

A minted key carries no authority of its own: it acts as the identity it was
minted for (see identities.py), and that identity's current role is resolved on
every request. Withdrawing an identity's authority — or deleting it — therefore
takes effect without touching the keys themselves.

Authority has two axes. The global role above is one; the other is per-team
(see teams.py), which lets a team administer itself without platform-wide
rights. `require_team_admin` is where the two meet: a global admin passes for
every team, a team admin passes for theirs, and nobody else passes at all.

All three front doors — REST, MCP, and A2A — resolve credentials through
`principal_from_headers`, so there is exactly one place to swap in a different
verifier (e.g. an MCP-spec OAuth 2.1 resource-server check) later.
"""

import hmac
import logging
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from . import personas
from .config import settings

log = logging.getLogger("cumulus.auth")

# Role precedence: lower index = more privileged.
ROLES = ["owner", "admin", "user"]


@dataclass
class Principal:
    uid: str
    email: str
    role: str
    # Set when the caller authenticated with a minted API key, so log lines and
    # audit trails can name the specific credential rather than just "api-key".
    key_id: str | None = None
    # Which persona this principal is acting as (personas.py). Read reach and
    # queue bindings come from it; `role` above stays the write ladder, so
    # every require_role call in the server is unaffected.
    #
    # These default to the LEAST privilege, never to a persona. Defaulting them
    # to the engineer meant a credential path that forgot to set them — as the
    # Firebase and minted-key paths both did — silently granted platform read
    # reach and every queue binding to whoever presented it. A field whose
    # default is "trusted" is a field that fails open.
    persona_id: str = ""
    scope: str = "own"
    queues: tuple[str, ...] = ()
    # The real operator, when this principal is the product of impersonation.
    # Every write records both, so the ledger never loses who actually acted.
    impersonated_by: str | None = None

    def outranks_or_is(self, role: str) -> bool:
        return ROLES.index(self.role) <= ROLES.index(role)

    @property
    def sees_everything(self) -> bool:
        """Whether reads may reach across the whole deployment."""
        return self.scope == "platform"

    @property
    def reads_beyond_self(self) -> bool:
        """Whether this principal may read facts about anyone but itself.

        The one rule, because three scopes read as two different binaries
        otherwise. `platform` and `tenant` both reach past the reader — the
        difference between them is *whose* deployment they reach into, not
        whether they may. Only `own` is confined. Gating one surface on
        `sees_everything` and its neighbour on `sees_only_own` put `tenant` on
        opposite sides of the same question, which is how a customer admin
        ended up seeing every expense row and none of the tasks about them.
        """
        return self.scope != "own"


    def owns_queue(self, queue: str) -> bool:
        return queue in self.queues


def principal_for(persona: personas.Persona, *, by: str | None = None) -> Principal:
    """The Principal a persona acts as.

    Impersonation rewrites the principal rather than travelling beside it, so
    everything downstream — the Employee Hub resolving "me", a queue filtering
    to yours, a claim recording its author — scopes correctly with no plumbing.
    """
    return Principal(
        uid=persona.uid,
        email=persona.email,
        role=persona.authority,
        persona_id=persona.id,
        scope=persona.scope,
        queues=persona.queues,
        impersonated_by=by,
    )


def flow_principal(flow_id: str, *queues: str) -> Principal:
    """The Principal a Flow commits as when no human authorized the write.

    The doctrine's Principal is "a human or an agent" and the Action Gateway's
    diagram lists a Flow among the actors, so a Flow gets an identity rather
    than an exemption. Its queues are declared at the flow: an invoice under
    the governed auto-post limit is committed on the AP flow's own authority,
    and removing that binding stops auto-posting rather than quietly waving it
    through. The lowest rung on the write ladder, deliberately — a Flow should
    reach only what its declared Actions permit.
    """
    return Principal(
        uid=f"flow:{flow_id}", email=f"{flow_id}@flow", role="user",
        scope="platform", queues=tuple(queues),
    )


# Local development signs in as the engineer, because the console's first job
# is to be developed against. A machine credential is scoped to the platform
# but belongs to no persona — it is not a person and must not appear as one.
_DEV_PRINCIPAL = principal_for(personas.ENGINEER)
_MACHINE_PRINCIPAL = Principal(
    uid="api-key", email="api-key@machine", role="owner",
    persona_id="", scope="platform", queues=(),
)


def _verify_firebase_token(token: str) -> Principal:
    from firebase_admin import auth as fb_auth  # deferred: not needed in dev mode

    from .firebase import ensure_firebase

    ensure_firebase()
    try:
        claims = fb_auth.verify_id_token(token)
    except Exception as exc:
        log.warning("ID token rejected: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Invalid or expired ID token")
    role = claims.get("role", "user")
    if role not in ROLES:
        role = "user"
    email = claims.get("email", "")
    reach = personas.reach_for(role)
    # Access from the credential; work from the roster. `reach_for` grants no
    # queues to any role — correctly, since deciding a Task is work assignment
    # and not authority — but it was reading the role and throwing the email
    # away, so a real account was bound to nothing even when the roster names
    # that exact person at a desk. See `queues_for_email`.
    reach["queues"] = queues_for(email, role)
    return Principal(uid=claims["uid"], email=email, role=role, **reach)


def queues_for(email: str, role: str) -> tuple[str, ...]:
    """Every desk a credential holds: declared on the roster, plus granted.

    Two sources, unioned, because they answer to different owners. The
    **roster** is Naviant's own fixture — the demo personas and the operator —
    and moves when the code does. The **queue registry** is the customer's,
    edited in the console by whoever administers the desk, and is the one that
    matters in a real deployment. Neither is authoritative over the other;
    holding a desk under either is holding it.
    """
    from .deps import get_queue_service  # deferred: queues imports auth

    held = list(personas.queues_for_email(email))
    try:
        for slug in get_queue_service().queues_for(email):
            if slug not in held:
                held.append(slug)
    except Exception:  # noqa: BLE001 — a registry outage must not lock everyone out
        log.exception("Queue registry unreadable; falling back to the roster")
    return tuple(held)


# Path segments that are literals in our routes; anything else is an id and gets
# collapsed, so per-endpoint usage counters stay bounded no matter how many
# assets or users exist.
_PATH_LITERALS = frozenset(
    {
        "api", "v1", "assets", "tree", "search", "users", "me", "role",
        "api-keys", "usage", "identities", "teams", "members", "assets",
        "flows", "stats", "runs", "audit", "evals", "fixtures", "preview",
        "ledger", "actions",
        "versions", "diff", "recommendations", "apply", "cases", "result",
        "run",
        "mcp", "a2a", "health", "healthz", "status", "reindex",
        ".well-known",
        "agent-card.json", "agent.json",
    }
)


def normalize_path(path: str) -> str:
    """`/api/v1/assets/vpn-setup-note` -> `/api/v1/assets/:id`."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "/"
    return "/" + "/".join(p if p in _PATH_LITERALS else ":id" for p in parts)


def _verify_minted_key(token: str, path: str | None) -> Principal | None:
    # Deferred imports: these modules import auth, so top-level would cycle.
    from .deps import get_api_key_directory, get_identity_service

    directory = get_api_key_directory()
    record = directory.verify(token)
    if record is None:
        return None
    if record.identity_uid is None:
        log.warning("Unattributed API key presented", extra={"keyId": record.id})
        return None
    identity = get_identity_service().get_cached(record.identity_uid)
    if identity is None:
        log.warning(
            "API key for a deleted identity presented",
            extra={"keyId": record.id, "identityUid": record.identity_uid},
        )
        return None
    if identity.disabled:
        log.warning(
            "API key for a disabled identity presented",
            extra={"keyId": record.id, "identityUid": identity.uid},
        )
        return None
    directory.record_usage(record.id, normalize_path(path or "/"))
    return Principal(
        uid=identity.uid,
        email=identity.email or identity.label,
        role=identity.role,
        key_id=record.id,
        # A key reads what its role reaches and decides nothing: `reach_for`
        # grants no queues to any role, and `require_human` refuses keys at
        # every decision point regardless.
        **personas.reach_for(identity.role),
    )


def _impersonate(real: Principal, persona_id: str) -> Principal:
    """Apply an `X-Cumulus-Persona` header, or refuse it.

    Two refusals, and both matter. A **machine credential may never
    impersonate**: a key picking a human identity is precisely the confusion
    the Action Gateway exists to prevent, and no integration has a reason to.
    And in a deployed environment only a `naviant-engineer` may impersonate at
    all — otherwise the header is a privilege escalation with a friendly name.
    Locally (`AUTH_BACKEND=none`) it is unrestricted, because that is the whole
    point of running locally.
    """
    persona = personas.get(persona_id)
    if persona is None:
        raise HTTPException(
            status_code=400, detail=f"Unknown persona '{persona_id}'"
        )
    if real.key_id is not None:
        raise HTTPException(
            status_code=403, detail="API keys cannot act as a persona."
        )
    if settings.auth_backend != "none" and not real.outranks_or_is("owner"):
        log.warning(
            "Impersonation refused",
            extra={"uid": real.uid, "role": real.role, "persona": persona_id},
        )
        raise HTTPException(
            status_code=403, detail="Only a Naviant engineer may act as a persona."
        )
    if persona.uid == real.uid:
        # Naming your own persona is not impersonation. Stamping
        # `impersonated_by` here would latch the console's "acting as" warning
        # on after the first switch and never clear it — and a warning that is
        # always on is not a warning.
        return real
    return principal_for(persona, by=real.uid)


def principal_from_headers(
    authorization: str | None,
    x_api_key: str | None,
    path: str | None = None,
    persona_id: str | None = None,
) -> Principal:
    """Resolve a Principal from raw header values, or raise HTTPException(401).

    Usable outside FastAPI's dependency system — the MCP transport middleware
    calls this directly with headers off the ASGI scope. `path` is used only to
    attribute API-key usage; omit it and usage is still counted, under "/".
    """
    real = _resolve_credential(authorization, x_api_key, path)
    if persona_id:
        return _impersonate(real, persona_id)
    return real


def _resolve_credential(
    authorization: str | None, x_api_key: str | None, path: str | None
) -> Principal:
    if (
        settings.admin_api_key
        and x_api_key
        and hmac.compare_digest(x_api_key, settings.admin_api_key)
    ):
        return _MACHINE_PRINCIPAL
    if x_api_key:
        principal = _verify_minted_key(x_api_key, path)
        if principal is not None:
            return principal
        # A supplied-but-unusable key is a hard failure: falling through to dev
        # mode would silently upgrade a revoked credential to owner.
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    if settings.auth_backend == "none":
        return _DEV_PRINCIPAL
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return _verify_firebase_token(authorization.split(" ", 1)[1])


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_cumulus_persona: str | None = Header(default=None),
) -> Principal:
    principal = principal_from_headers(
        authorization, x_api_key, request.url.path, x_cumulus_persona
    )
    # Read back by RequestContextMiddleware so each access-log line names the
    # caller (and the key id, when a minted key was used).
    request.state.principal = principal
    return principal


def require_role(principal: Principal, role: str) -> Principal:
    if not principal.outranks_or_is(role):
        log.warning(
            "Authorization denied",
            extra={"uid": principal.uid, "role": principal.role, "required": role},
        )
        raise HTTPException(status_code=403, detail=f"Requires {role} role or higher")
    return principal


def require_reader(principal: Principal) -> Principal:
    """Any authenticated principal may read; the lowest role is `user`."""
    return require_role(principal, "user")


def require_human(principal: Principal, act: str) -> Principal:
    """Refuse a minted key. `act` completes "API keys cannot {act}".

    Eight modules each kept their own copy of this, which meant eight chances
    for the rule to drift. The rule itself never differed — only the sentence.
    """
    if principal.key_id is not None:
        log.warning(
            "API key attempted a human-only act",
            extra={"keyId": principal.key_id, "act": act},
        )
        raise HTTPException(status_code=403, detail=f"API keys cannot {act}.")
    return principal


def require_queue(principal: Principal, queue: str) -> Principal:
    """Gate a Task decision on the queue actually being this principal's work.

    The doctrine's Role confers permissions *and* queue-bindings, and this is
    the second half. Without it "who may decide this" collapses back into the
    write ladder, which cannot tell an AP clerk from an expense approver — they
    sit on the same rung and own different work.
    """
    if principal.owns_queue(queue):
        return principal
    log.warning(
        "Queue authorization denied",
        extra={"uid": principal.uid, "persona": principal.persona_id,
               "queue": queue, "queues": list(principal.queues)},
    )
    raise HTTPException(
        status_code=403,
        detail=f"The '{queue}' queue is not this persona's work.",
    )


def team_role_of(principal: Principal, slug: str) -> str | None:
    """This principal's role inside one team, or None if not a member."""
    from .deps import get_team_service  # deferred: teams imports auth

    return get_team_service().team_role(principal.uid, slug)


def is_team_admin(principal: Principal, slug: str) -> bool:
    """True for a platform admin anywhere, or a team admin on their own team.

    A minted key never counts as a team admin. It may act *as* an identity that
    administers a team, but self-service administration is a human action — the
    same reason api_keys.py refuses to let a key manage keys.
    """
    if principal.key_id is None and principal.outranks_or_is("admin"):
        return True
    if principal.key_id is not None:
        return False
    return team_role_of(principal, slug) == "admin"


def require_team_admin(principal: Principal, slug: str) -> Principal:
    """Gate a team-scoped action on platform-admin or admin of *that* team."""
    if is_team_admin(principal, slug):
        return principal
    log.warning(
        "Team authorization denied",
        extra={"uid": principal.uid, "role": principal.role, "team": slug},
    )
    raise HTTPException(
        status_code=403,
        detail=f"Requires admin of team '{slug}', or platform admin",
    )


def require_any_team_admin(principal: Principal) -> Principal:
    """For reads that need *some* team authority before a team is named.

    A platform admin passes whatever it authenticated with — this is a read
    gate, and a key carrying admin authority could always read the roster.
    Keys get no *team* authority, though: team administration is a human act,
    so routes that mutate pair this with `_require_human`.
    """
    from .deps import get_team_service  # deferred: teams imports auth

    if principal.outranks_or_is("admin"):
        return principal
    if principal.key_id is None and get_team_service().administered_by(principal.uid):
        return principal
    raise HTTPException(
        status_code=403,
        detail="Requires platform admin, or admin of at least one team",
    )
