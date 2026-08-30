"""Identity management: list every holder of credentials, and manage the
non-person ones.

People are created and password-managed through /api/v1/users (they are
Firebase Auth accounts); they appear here so that one screen can show every
identity that can hold a key. Agents and service clients are created here — they
have no password and can never sign in, they only hold keys.

Authorization mirrors user management: admin+ to see and manage identities,
owner to create, re-scope, or delete anything holding admin authority.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from .auth import (
    Principal,
    get_principal,
    require_human,
    require_any_team_admin,
    require_role,
    require_team_admin,
)
from .deps import get_api_key_directory, get_identity_service, get_team_service
from .identities import (
    Identity,
    IdentityExistsError,
    IdentityNotFoundError,
)

log = logging.getLogger("cumulus.identities.api")

router = APIRouter(prefix="/api/v1/identities", tags=["identities"])

MachineKind = Literal["agent", "service"]
MachineRole = Literal["user", "admin"]
Role = Literal["owner", "admin", "user"]


class MachineIdentityCreate(BaseModel):
    kind: MachineKind = "agent"
    display_name: str = Field(min_length=1, max_length=120)
    role: MachineRole = "user"
    description: str = Field(default="", max_length=400)
    owner: str = Field(
        default="", max_length=120, description="Person accountable for this client"
    )
    team: str = Field(
        default="",
        max_length=60,
        description="Team this client belongs to; required for team-admin self-service",
    )


class RoleChange(BaseModel):
    role: Role


def _require_can_touch(principal: Principal, role: str) -> None:
    """Granting or withdrawing admin-or-higher authority is an owner action."""
    if role in ("owner", "admin"):
        require_role(principal, "owner")


@router.get("", response_model=list[Identity])
def list_identities(principal: Principal = Depends(get_principal)) -> list[Identity]:
    # Team admins need the roster to pick who to add to their team; membership
    # is the only thing that grant buys them.
    require_any_team_admin(principal)
    return get_identity_service().list()


@router.post("", response_model=Identity, status_code=201)
def create_machine_identity(
    payload: MachineIdentityCreate, principal: Principal = Depends(get_principal)
) -> Identity:
    require_human(principal, "manage identities; sign in as an admin")
    if payload.team:
        # Self-service path: a team admin may register a client for their own
        # team without platform-wide rights.
        require_team_admin(principal, payload.team)
        if get_team_service().get(payload.team) is None:
            raise HTTPException(
                status_code=404, detail=f"Team not found: {payload.team}"
            )
    else:
        require_role(principal, "admin")
    _require_can_touch(principal, payload.role)
    try:
        identity = get_identity_service().create_machine(
            kind=payload.kind,
            display_name=payload.display_name.strip(),
            role=payload.role,
            description=payload.description.strip(),
            owner=payload.owner.strip(),
            created_by=principal.email or principal.uid,
        )
    except IdentityExistsError:
        raise HTTPException(
            status_code=409,
            detail=f"An identity named '{payload.display_name}' already exists",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if payload.team:
        get_team_service().set_member(
            payload.team,
            identity.uid,
            "member",
            added_by=principal.email or principal.uid,
        )
    log.info(
        "Identity created",
        extra={
            "identityUid": identity.uid,
            "kind": identity.kind,
            "grantedRole": identity.role,
            "team": payload.team or None,
            "actor": principal.uid,
        },
    )
    return identity


@router.put("/{uid}/role", response_model=Identity)
def change_identity_role(
    uid: str, payload: RoleChange, principal: Principal = Depends(get_principal)
) -> Identity:
    require_role(principal, "admin")
    require_human(principal, "manage identities; sign in as an admin")
    if uid == principal.uid:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    service = get_identity_service()
    target = service.get(uid)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Identity not found: {uid}")
    _require_can_touch(principal, target.role)
    _require_can_touch(principal, payload.role)
    try:
        updated = service.set_role(uid, payload.role)
    except IdentityNotFoundError:
        raise HTTPException(status_code=404, detail=f"Identity not found: {uid}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log.info(
        "Identity role changed",
        extra={
            "identityUid": uid,
            "fromRole": target.role,
            "toRole": updated.role,
            "actor": principal.uid,
        },
    )
    return updated


@router.delete("/{uid}", status_code=204)
def delete_identity(uid: str, principal: Principal = Depends(get_principal)) -> None:
    """Deleting an identity revokes every key it holds, in that order."""
    require_role(principal, "admin")
    require_human(principal, "manage identities; sign in as an admin")
    if uid == principal.uid:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    service = get_identity_service()
    target = service.get(uid)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Identity not found: {uid}")
    _require_can_touch(principal, target.role)

    # Revoke first: a key whose identity vanished is already refused at the
    # door, but leaving it un-revoked would misreport it as live in the UI.
    revoked = get_api_key_directory().revoke_for_identity(
        uid, principal.email or principal.uid
    )
    try:
        service.delete(uid)
    except IdentityNotFoundError:
        raise HTTPException(status_code=404, detail=f"Identity not found: {uid}")
    # Drop the memberships too, or the team roster keeps a row pointing at
    # nothing and every team read has to reason about ghosts.
    dropped = get_team_service().remove_identity_everywhere(uid)
    log.info(
        "Identity deleted",
        extra={
            "identityUid": uid,
            "kind": target.kind,
            "keysRevoked": revoked,
            "teamsLeft": dropped,
            "actor": principal.uid,
        },
    )
