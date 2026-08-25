"""User management API: add, remove, and change roles (owner/admin/user).

Rules: admins can manage plain users; anything touching an admin or owner
account (creating, deleting, or granting those roles) requires the owner role.
Nobody can delete themselves or change their own role.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from typing import Literal

from .auth import Principal, get_principal, require_role
from .deps import (
    get_api_key_directory,
    get_identity_service,
    get_team_service,
    get_user_directory,
)
from .users import PlatformUser, UserExistsError, UserNotFoundError

log = logging.getLogger("cumulus.users")

router = APIRouter(prefix="/api/v1/users", tags=["users"])

Role = Literal["owner", "admin", "user"]


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = ""
    role: Role = "user"


class RoleChange(BaseModel):
    role: Role


def _require_can_touch(principal: Principal, target_role: str) -> None:
    if target_role in ("owner", "admin"):
        require_role(principal, "owner")


@router.get("", response_model=list[PlatformUser])
def list_users(principal: Principal = Depends(get_principal)) -> list[PlatformUser]:
    require_role(principal, "admin")
    return get_user_directory().list()


@router.post("", response_model=PlatformUser, status_code=201)
def create_user(
    payload: UserCreate, principal: Principal = Depends(get_principal)
) -> PlatformUser:
    require_role(principal, "admin")
    _require_can_touch(principal, payload.role)
    try:
        user = get_user_directory().create(
            payload.email, payload.password, payload.display_name, payload.role
        )
    except UserExistsError:
        raise HTTPException(
            status_code=409, detail=f"User already exists: {payload.email}"
        )
    log.info(
        "User created",
        extra={"targetUid": user.uid, "grantedRole": user.role, "actor": principal.uid},
    )
    return user


@router.put("/{uid}/role", response_model=PlatformUser)
def change_role(
    uid: str, payload: RoleChange, principal: Principal = Depends(get_principal)
) -> PlatformUser:
    require_role(principal, "admin")
    if uid == principal.uid:
        raise HTTPException(status_code=400, detail="You cannot change your own role")
    directory = get_user_directory()
    target = directory.get(uid)
    if target is None:
        raise HTTPException(status_code=404, detail=f"User not found: {uid}")
    _require_can_touch(principal, target.role)
    _require_can_touch(principal, payload.role)
    updated = directory.set_role(uid, payload.role)
    log.info(
        "User role changed",
        extra={
            "targetUid": uid,
            "fromRole": target.role,
            "toRole": updated.role,
            "actor": principal.uid,
        },
    )
    return updated


@router.delete("/{uid}", status_code=204)
def delete_user(uid: str, principal: Principal = Depends(get_principal)) -> None:
    require_role(principal, "admin")
    if uid == principal.uid:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    directory = get_user_directory()
    target = directory.get(uid)
    if target is None:
        raise HTTPException(status_code=404, detail=f"User not found: {uid}")
    _require_can_touch(principal, target.role)
    # Deleting a person here must do what deleting them through /identities
    # does — the two routes remove the same thing, so they must leave the same
    # state behind: no live credentials, and no memberships pointing at a ghost.
    revoked = get_api_key_directory().revoke_for_identity(
        uid, principal.email or principal.uid
    )
    try:
        directory.delete(uid)
    except UserNotFoundError:
        raise HTTPException(status_code=404, detail=f"User not found: {uid}")
    dropped = get_team_service().remove_identity_everywhere(uid)
    get_identity_service().invalidate(uid)
    log.info(
        "User deleted",
        extra={
            "targetUid": uid,
            "targetRole": target.role,
            "keysRevoked": revoked,
            "teamsLeft": dropped,
            "actor": principal.uid,
        },
    )


@router.get("/me", response_model=Principal)
def whoami(principal: Principal = Depends(get_principal)) -> Principal:
    return principal
