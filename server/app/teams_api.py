"""Team management: the roster that owns knowledge and administers itself.

Authorization here is the two-axis model described in teams.py. Reading teams
needs any authenticated role — knowing who owns a document is part of reading
the corpus. Creating and deleting teams is a platform-admin action, because a
team is a container of authority. Everything *inside* a team — its details, its
membership — is open to that team's own admins, which is the self-service the
model exists to provide.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from .auth import (
    Principal,
    get_principal,
    require_human,
    is_team_admin,
    require_reader,
    require_role,
    require_team_admin,
)
from .deps import get_identity_service, get_service, get_team_service
from .identities import Identity
from .models import AssetSummary
from .teams import Team, TeamExistsError, TeamNotFoundError

log = logging.getLogger("cumulus.teams.api")

router = APIRouter(prefix="/api/v1/teams", tags=["teams"])

TeamRole = Literal["admin", "member"]


class TeamCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=60)
    name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=400)


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=400)


class MemberChange(BaseModel):
    team_role: TeamRole = "member"


class MemberPublic(BaseModel):
    """A member with the identity fields the console needs to render a row."""

    identity_uid: str
    label: str
    kind: str
    email: str = ""
    # The member's platform-wide role, which its keys inherit.
    global_role: str = "user"
    team_role: str = "member"
    disabled: bool = False
    added_at: str = ""
    added_by: str = ""
    # True when the identity has been deleted but a stale membership remains.
    missing: bool = False


class TeamPublic(BaseModel):
    slug: str
    name: str
    description: str = ""
    # Empty for readers who may not see the roster; `member_count` is always
    # populated so the team still renders.
    members: list[MemberPublic] = Field(default_factory=list)
    member_count: int = 0
    asset_count: int = 0
    created_at: str = ""
    created_by: str = ""
    # The calling principal's role in this team, so the UI can decide what to
    # offer without replaying the permission rules itself.
    my_team_role: str | None = None
    can_administer: bool = False


def _may_see_roster(principal: Principal, team: Team) -> bool:
    """Who owns a document is public to readers; who is *on* the team is not.

    The roster carries emails, platform roles, and disabled flags — the same
    material `/api/v1/identities` gates. Platform admins and the team's own
    members see it; everyone else gets the count.
    """
    return (
        principal.outranks_or_is("admin")
        or team.role_of(principal.uid) is not None
    )


def _public(
    team: Team,
    principal: Principal,
    identities: dict[str, Identity] | None = None,
    asset_count: int | None = None,
) -> TeamPublic:
    if identities is None:
        identities = {i.uid: i for i in get_identity_service().list()}
    if asset_count is None:
        asset_count = _asset_count(team.slug)
    if not _may_see_roster(principal, team):
        return TeamPublic(
            slug=team.slug,
            name=team.name,
            description=team.description,
            member_count=len(team.members),
            asset_count=asset_count,
            created_at=team.created_at.isoformat(),
            created_by=team.created_by,
            my_team_role=None,
            can_administer=False,
        )

    members = []
    for member in sorted(
        team.members, key=lambda m: (m.team_role != "admin", m.identity_uid)
    ):
        identity = identities.get(member.identity_uid)
        members.append(
            MemberPublic(
                identity_uid=member.identity_uid,
                label=identity.label if identity else member.identity_uid,
                kind=identity.kind if identity else "",
                email=identity.email if identity else "",
                global_role=identity.role if identity else "user",
                team_role=member.team_role,
                disabled=identity.disabled if identity else False,
                added_at=member.added_at.isoformat(),
                added_by=member.added_by,
                missing=identity is None,
            )
        )
    return TeamPublic(
        slug=team.slug,
        name=team.name,
        description=team.description,
        members=members,
        member_count=len(members),
        asset_count=asset_count,
        created_at=team.created_at.isoformat(),
        created_by=team.created_by,
        my_team_role=team.role_of(principal.uid),
        can_administer=is_team_admin(principal, team.slug),
    )


def _asset_count(slug: str) -> int:
    return sum(1 for a in get_service().repo.all() if a.owner == slug)


@router.get("", response_model=list[TeamPublic])
def list_teams(principal: Principal = Depends(get_principal)) -> list[TeamPublic]:
    require_reader(principal)
    # Resolve identities and asset counts once for the whole list rather than
    # per team: both are full scans of their store.
    identities = {i.uid: i for i in get_identity_service().list()}
    counts: dict[str, int] = {}
    for asset in get_service().repo.all():
        if asset.owner:
            counts[asset.owner] = counts.get(asset.owner, 0) + 1
    return [
        _public(t, principal, identities, counts.get(t.slug, 0))
        for t in get_team_service().list()
    ]


@router.post("", response_model=TeamPublic, status_code=201)
def create_team(
    payload: TeamCreate, principal: Principal = Depends(get_principal)
) -> TeamPublic:
    # Creating a team creates a container of authority, so it stays a platform
    # action even though everything inside the team is self-served.
    require_role(principal, "admin")
    require_human(principal, "manage teams; sign in as an admin")
    try:
        team = get_team_service().create(
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            created_by=principal.email or principal.uid,
        )
    except TeamExistsError:
        raise HTTPException(
            status_code=409, detail=f"A team named '{payload.slug}' already exists"
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log.info("Team created", extra={"team": team.slug, "actor": principal.uid})
    return _public(team, principal)


@router.get("/{slug}", response_model=TeamPublic)
def get_team(slug: str, principal: Principal = Depends(get_principal)) -> TeamPublic:
    require_reader(principal)
    team = get_team_service().get(slug)
    if team is None:
        raise HTTPException(status_code=404, detail=f"Team not found: {slug}")
    return _public(team, principal)


@router.get("/{slug}/assets", response_model=list[AssetSummary])
def team_assets(
    slug: str, principal: Principal = Depends(get_principal)
) -> list[AssetSummary]:
    require_reader(principal)
    if get_team_service().get(slug) is None:
        raise HTTPException(status_code=404, detail=f"Team not found: {slug}")
    return [
        a.summary()
        for a in sorted(get_service().repo.all(), key=lambda a: a.path)
        if a.owner == slug
    ]


@router.patch("/{slug}", response_model=TeamPublic)
def update_team(
    slug: str, payload: TeamUpdate, principal: Principal = Depends(get_principal)
) -> TeamPublic:
    require_human(principal, "manage teams; sign in as an admin")
    require_team_admin(principal, slug)
    try:
        team = get_team_service().update_details(
            slug, payload.name, payload.description
        )
    except TeamNotFoundError:
        raise HTTPException(status_code=404, detail=f"Team not found: {slug}")
    log.info("Team updated", extra={"team": slug, "actor": principal.uid})
    return _public(team, principal)


@router.delete("/{slug}", status_code=204)
def delete_team(slug: str, principal: Principal = Depends(get_principal)) -> None:
    require_role(principal, "admin")
    require_human(principal, "manage teams; sign in as an admin")
    owned = _asset_count(slug)
    if owned:
        # Deleting the owner would silently orphan documents. Make the caller
        # reassign them first, so ownership is never lost by accident.
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{slug}' still owns {owned} "
                f"{'asset' if owned == 1 else 'assets'}. Reassign them to "
                "another team first."
            ),
        )
    try:
        get_team_service().delete(slug)
    except TeamNotFoundError:
        raise HTTPException(status_code=404, detail=f"Team not found: {slug}")
    log.info("Team deleted", extra={"team": slug, "actor": principal.uid})


@router.put("/{slug}/members/{identity_uid}", response_model=TeamPublic)
def set_member(
    slug: str,
    identity_uid: str,
    payload: MemberChange,
    principal: Principal = Depends(get_principal),
) -> TeamPublic:
    """Add a member, or change the role of one already on the team.

    This grants *team* authority only. Global roles are untouched here, and
    remain a platform-admin action — that separation is what keeps team admin
    from being a path to platform admin.
    """
    require_human(principal, "manage teams; sign in as an admin")
    require_team_admin(principal, slug)
    service = get_team_service()
    if service.get(slug) is None:
        raise HTTPException(status_code=404, detail=f"Team not found: {slug}")

    identity = get_identity_service().get(identity_uid)
    if identity is None:
        raise HTTPException(
            status_code=404, detail=f"Identity not found: {identity_uid}"
        )
    if payload.team_role == "admin" and not identity.is_person:
        # Same principle that caps machine identities below owner: a credential
        # must never be able to grant access to anything.
        raise HTTPException(
            status_code=400,
            detail=(
                "Only a person can administer a team; agents and services may "
                "be members."
            ),
        )

    # Demotion orphans a team exactly as removal does, and the console offers
    # it on your own row — so it needs the same guard.
    current = service.get(slug)
    if (
        payload.team_role != "admin"
        and current is not None
        and current.role_of(identity_uid) == "admin"
        and len(current.admins()) == 1
        and not principal.outranks_or_is("admin")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{slug}' would be left with no admin. Promote another member "
                "first."
            ),
        )
    try:
        team = service.set_member(
            slug,
            identity_uid,
            payload.team_role,
            added_by=principal.email or principal.uid,
        )
    except TeamNotFoundError:
        raise HTTPException(status_code=404, detail=f"Team not found: {slug}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log.info(
        "Team member set",
        extra={
            "team": slug,
            "identityUid": identity_uid,
            "teamRole": payload.team_role,
            "actor": principal.uid,
        },
    )
    return _public(team, principal)


@router.delete("/{slug}/members/{identity_uid}", response_model=TeamPublic)
def remove_member(
    slug: str, identity_uid: str, principal: Principal = Depends(get_principal)
) -> TeamPublic:
    require_human(principal, "manage teams; sign in as an admin")
    require_team_admin(principal, slug)
    service = get_team_service()
    team = service.get(slug)
    if team is None:
        raise HTTPException(status_code=404, detail=f"Team not found: {slug}")

    member = team.member(identity_uid)
    if member is None:
        raise HTTPException(
            status_code=404, detail=f"'{identity_uid}' is not on team '{slug}'"
        )
    # Removing the last admin leaves a team nobody but a platform admin can
    # run, which defeats self-service. A platform admin may still do it — they
    # are the ones who would have to clean it up.
    if (
        member.team_role == "admin"
        and len(team.admins()) == 1
        and not principal.outranks_or_is("admin")
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{slug}' would be left with no admin. Promote another member "
                "first."
            ),
        )
    team = service.remove_member(slug, identity_uid)
    log.info(
        "Team member removed",
        extra={"team": slug, "identityUid": identity_uid, "actor": principal.uid},
    )
    return _public(team, principal)
