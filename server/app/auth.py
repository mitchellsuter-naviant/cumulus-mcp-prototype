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

    def outranks_or_is(self, role: str) -> bool:
        return ROLES.index(self.role) <= ROLES.index(role)


_DEV_PRINCIPAL = Principal(uid="dev", email="dev@localhost", role="owner")
_MACHINE_PRINCIPAL = Principal(uid="api-key", email="api-key@machine", role="owner")


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
    return Principal(uid=claims["uid"], email=claims.get("email", ""), role=role)


# Path segments that are literals in our routes; anything else is an id and gets
# collapsed, so per-endpoint usage counters stay bounded no matter how many
# assets or users exist.
_PATH_LITERALS = frozenset(
    {
        "api", "v1", "assets", "tree", "search", "users", "me", "role",
        "api-keys", "usage", "identities", "teams", "members", "assets",
        "workflows", "stats", "runs", "audit", "golden-set", "test-runs",
        "versions", "diff", "recommendations", "apply",
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
    )


def principal_from_headers(
    authorization: str | None, x_api_key: str | None, path: str | None = None
) -> Principal:
    """Resolve a Principal from raw header values, or raise HTTPException(401).

    Usable outside FastAPI's dependency system — the MCP transport middleware
    calls this directly with headers off the ASGI scope. `path` is used only to
    attribute API-key usage; omit it and usage is still counted, under "/".
    """
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
) -> Principal:
    principal = principal_from_headers(authorization, x_api_key, request.url.path)
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
