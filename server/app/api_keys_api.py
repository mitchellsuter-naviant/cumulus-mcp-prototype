"""API-key management: mint, list, revoke, and inspect usage.

Every key is minted for exactly one identity (see identities.py) and inherits
that identity's role, so a credential is always attributable and its authority
has exactly one home.

Authorization mirrors user management — admin+ to manage keys, owner to touch an
admin-scoped key. One extra rule: a *minted* key may never mint or revoke keys.
Without that, any admin-scoped key could clone itself indefinitely and revocation
would stop being a containment tool. The legacy ADMIN_API_KEY is still allowed
through, since it is already an owner-level root credential and is the bootstrap
path for the first key.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .api_keys import (
    ApiKeyNotFoundError,
    ApiKeyPublic,
    UsageDay,
)
from .auth import (
    Principal,
    get_principal,
    require_human,
    is_team_admin,
    require_any_team_admin,
    require_role,
)
from .config import settings
from .deps import get_api_key_directory, get_identity_service, get_team_service
from .identities import Identity
from .models import utcnow

log = logging.getLogger("cumulus.apikeys.api")

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])

class ApiKeyCreate(BaseModel):
    name: str = Field(
        min_length=1, max_length=120, description="What this credential is, e.g. the client"
    )
    identity_uid: str = Field(
        min_length=1, description="Who or what the key acts as; supplies its role"
    )
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyMinted(BaseModel):
    """The only response that ever carries the secret."""

    key: ApiKeyPublic
    token: str = Field(description="Shown once; not recoverable afterwards")
    mcp_config: dict = Field(description="Ready-to-paste MCP client configuration")


def _require_can_touch(principal: Principal, key_role: str) -> None:
    """Granting or revoking admin-scoped keys is an owner action.

    This bound holds regardless of team authority: administering a team never
    lifts the ceiling on what a credential may carry.
    """
    if key_role == "admin":
        require_role(principal, "owner")


def _administers_holder(principal: Principal, identity_uid: str | None) -> bool:
    """True when the caller may manage this holder's keys by team authority.

    Deliberately false for people. A key minted for a person *authenticates as
    that person*, and a team admin can add anyone to their own team — so
    allowing this would let them pull someone onto the team and then mint a
    credential wearing that person's identity. Team authority extends to the
    team's machines only; a person's credentials stay a platform-admin matter.
    """
    if not identity_uid:
        return False
    identity = get_identity_service().get(identity_uid)
    if identity is None or identity.is_person:
        return False
    return any(
        is_team_admin(principal, team.slug)
        for team in get_team_service().teams_for(identity_uid)
    )


def _require_can_manage(principal: Principal, identity_uid: str | None) -> None:
    """Platform admins manage every key; team admins manage their machines'.

    Keys are the credential half of self-service: whoever may put an agent on a
    team may also give that agent a way in, and take it away again.
    """
    if principal.outranks_or_is("admin") or _administers_holder(
        principal, identity_uid
    ):
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "Requires admin role, or admin of a team that owns this "
            "agent or service"
        ),
    )


def _resolve_identity(uid: str) -> Identity:
    identity = get_identity_service().get(uid)
    if identity is None:
        raise HTTPException(status_code=404, detail=f"Identity not found: {uid}")
    return identity


def _public(record, identities: dict[str, Identity]):
    """Attach the holder's name and current role to a stored key."""
    identity = identities.get(record.identity_uid) if record.identity_uid else None
    if identity is None:
        # Unattributed, or the identity was deleted out from under the key —
        # either way it authenticates nothing, so it carries no role.
        return record.public()
    return record.public(
        identity_name=identity.label,
        identity_kind=identity.kind,
        role=identity.role,
    )


def _mcp_config(token: str, name: str) -> dict:
    return {
        "servers": {
            "cumulus-knowledge": {
                "type": "http",
                "url": f"{settings.public_base_url.rstrip('/')}/mcp/",
                "headers": {"X-API-Key": token},
            }
        },
        "_comment": f"Cumulus knowledge layer — key '{name}'",
    }


@router.get("", response_model=list[ApiKeyPublic])
def list_keys(principal: Principal = Depends(get_principal)) -> list[ApiKeyPublic]:
    require_any_team_admin(principal)
    require_human(principal, "manage API keys; sign in as an admin")
    identities = {i.uid: i for i in get_identity_service().list()}
    keys = get_api_key_directory().list()
    if not principal.outranks_or_is("admin"):
        # A team admin sees the credentials they are accountable for, and no
        # others. Orphaned keys carry no holder, so they stay platform-only.
        keys = [k for k in keys if _administers_holder(principal, k.identity_uid)]
    return [_public(k, identities) for k in keys]


@router.post("", response_model=ApiKeyMinted, status_code=201)
def create_key(
    payload: ApiKeyCreate, principal: Principal = Depends(get_principal)
) -> ApiKeyMinted:
    require_human(principal, "manage API keys; sign in as an admin")
    identity = _resolve_identity(payload.identity_uid)
    _require_can_manage(principal, identity.uid)
    if identity.disabled:
        raise HTTPException(
            status_code=400,
            detail=f"'{identity.label}' is disabled and cannot hold a key",
        )
    if identity.role == "owner":
        # Owner authority is a human sign-in privilege; a file on disk must not
        # carry it. Mint against an admin identity instead.
        raise HTTPException(
            status_code=400,
            detail="Owner identities cannot hold API keys; use an admin identity.",
        )
    _require_can_touch(principal, identity.role)

    expires_at = None
    if payload.expires_in_days is not None:
        from datetime import timedelta

        expires_at = utcnow() + timedelta(days=payload.expires_in_days)

    try:
        record, token = get_api_key_directory().create(
            payload.name.strip(),
            identity.uid,
            principal.email or principal.uid,
            expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    log.info(
        "API key minted",
        extra={
            "keyId": record.id,
            "keyName": record.name,
            "identityUid": identity.uid,
            "identityKind": identity.kind,
            "grantedRole": identity.role,
            "expiresAt": record.expires_at.isoformat() if record.expires_at else None,
            "actor": principal.uid,
        },
    )
    return ApiKeyMinted(
        key=record.public(
            identity_name=identity.label,
            identity_kind=identity.kind,
            role=identity.role,
        ),
        token=token,
        mcp_config=_mcp_config(token, record.name),
    )


@router.delete("/{key_id}", status_code=204)
def revoke_key(key_id: str, principal: Principal = Depends(get_principal)) -> None:
    require_human(principal, "manage API keys; sign in as an admin")
    directory = get_api_key_directory()
    existing = directory.get(key_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"API key not found: {key_id}")
    _require_can_manage(principal, existing.identity_uid)
    holder = (
        get_identity_service().get(existing.identity_uid)
        if existing.identity_uid
        else None
    )
    _require_can_touch(principal, holder.role if holder else "user")
    try:
        directory.revoke(key_id, principal.email or principal.uid)
    except ApiKeyNotFoundError:
        raise HTTPException(status_code=404, detail=f"API key not found: {key_id}")
    log.info(
        "API key revoked",
        extra={"keyId": key_id, "keyName": existing.name, "actor": principal.uid},
    )


@router.get("/{key_id}/usage", response_model=list[UsageDay])
def key_usage(
    key_id: str,
    days: int = Query(default=30, ge=1, le=365),
    principal: Principal = Depends(get_principal),
) -> list[UsageDay]:
    require_human(principal, "manage API keys; sign in as an admin")
    directory = get_api_key_directory()
    existing = directory.get(key_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"API key not found: {key_id}")
    _require_can_manage(principal, existing.identity_uid)
    return directory.usage(key_id, days)
