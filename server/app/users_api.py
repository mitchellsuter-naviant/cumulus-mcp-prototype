"""Creating a person: the one act that is a Firebase Auth account, not a row.

Two routes, and both are about the account itself — minting one with a
password, and echoing back who the caller is. Everything *else* about a
person — listing them, changing their role, deleting them — is an identity
act, and lives on `/api/v1/identities` beside the same acts for agents and
service clients. This module used to carry a second spelling of all three,
which is two authorization policies over one directory waiting to drift apart.

Rules: admins may create plain users; minting an admin or owner account
requires the owner role.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from typing import Literal

from .auth import Principal, get_principal, require_role
from .deps import get_user_directory
from .users import PlatformUser, UserExistsError

log = logging.getLogger("cumulus.users")

router = APIRouter(prefix="/api/v1/users", tags=["users"])

Role = Literal["owner", "admin", "user"]


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = ""
    role: Role = "user"


def _require_can_touch(principal: Principal, target_role: str) -> None:
    if target_role in ("owner", "admin"):
        require_role(principal, "owner")


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


@router.get("/me", response_model=Principal)
def whoami(principal: Principal = Depends(get_principal)) -> Principal:
    return principal
