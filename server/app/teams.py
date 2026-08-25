"""Teams: the unit that owns knowledge, and the unit that self-serves access.

A team is the durable owner of assets. People move on; `platform-eng` still owns
the incident-response standard, so ownership is modelled on the team rather than
on whoever happened to write the document.

Teams are also the second axis of authorization. Authority here is a pair:

  * a **global role** (owner / admin / user) — what you may do to the platform
  * a **team role** (admin / member) — what you may do inside one team

A team admin can run their own corner without being a platform admin: add and
remove that team's members, register agents for it, mint and revoke those
agents' keys, and edit the assets the team owns. Two rules keep that from
becoming a privilege-escalation path, and both mirror constraints the key
system already enforces:

  1. A team admin grants *team* membership only, never global authority. The
     global-role endpoint stays at platform-admin and above.
  2. Team-scoped power is still bounded by the actor's global role, so a `user`
     who administers a team cannot mint a credential carrying admin authority.

Membership covers machines as well as people — an agent belongs to the team
accountable for it — but only a person may hold `admin` on a team, for the same
reason a machine identity is capped below owner: a credential file must never be
able to grant access.

The team's `slug` is deliberately the same string that lives in `Asset.owner`.
The corpus already carried values like `platform-eng`; making the slug that
string turns existing data into real ownership with no migration.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .models import utcnow

log = logging.getLogger("cumulus.teams")

TeamRole = Literal["admin", "member"]

# Same shape as an asset id: lowercase, hyphenated, url-safe.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


class TeamMember(BaseModel):
    identity_uid: str
    team_role: TeamRole = "member"
    added_at: datetime = Field(default_factory=utcnow)
    added_by: str = ""


class Team(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    name: str
    description: str = ""
    members: list[TeamMember] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""

    def member(self, identity_uid: str) -> TeamMember | None:
        return next(
            (m for m in self.members if m.identity_uid == identity_uid), None
        )

    def role_of(self, identity_uid: str) -> str | None:
        member = self.member(identity_uid)
        return member.team_role if member else None

    def admins(self) -> list[TeamMember]:
        return [m for m in self.members if m.team_role == "admin"]


class TeamExistsError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Team already exists: {slug}")
        self.slug = slug


class TeamNotFoundError(Exception):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Team not found: {slug}")
        self.slug = slug


class TeamDirectory(Protocol):
    def list(self) -> list[Team]: ...
    def get(self, slug: str) -> Team | None: ...
    def create(self, team: Team) -> Team: ...
    def update(self, team: Team) -> Team: ...
    def delete(self, slug: str) -> None: ...


class InMemoryTeamDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Team] = {}

    def list(self) -> list[Team]:
        with self._lock:
            return sorted(self._items.values(), key=lambda t: t.name.lower())

    def get(self, slug: str) -> Team | None:
        with self._lock:
            return self._items.get(slug)

    def create(self, team: Team) -> Team:
        with self._lock:
            if team.slug in self._items:
                raise TeamExistsError(team.slug)
            self._items[team.slug] = team
            return team

    def update(self, team: Team) -> Team:
        with self._lock:
            if team.slug not in self._items:
                raise TeamNotFoundError(team.slug)
            self._items[team.slug] = team
            return team

    def delete(self, slug: str) -> None:
        with self._lock:
            if slug not in self._items:
                raise TeamNotFoundError(slug)
            del self._items[slug]


class FirestoreTeamDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[Team]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(Team.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable team", extra={"docId": doc.id})
        return sorted(out, key=lambda t: t.name.lower())

    def get(self, slug: str) -> Team | None:
        snap = self._col.document(slug).get()
        if not snap.exists:
            return None
        try:
            return Team.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable team document", extra={"teamSlug": slug})
            return None

    def create(self, team: Team) -> Team:
        ref = self._col.document(team.slug)
        if ref.get().exists:
            raise TeamExistsError(team.slug)
        ref.set(team.model_dump(mode="json"))
        return team

    def update(self, team: Team) -> Team:
        ref = self._col.document(team.slug)
        if not ref.get().exists:
            raise TeamNotFoundError(team.slug)
        ref.set(team.model_dump(mode="json"))
        return team

    def delete(self, slug: str) -> None:
        ref = self._col.document(slug)
        if not ref.get().exists:
            raise TeamNotFoundError(slug)
        ref.delete()


def slugify(name: str) -> str:
    """`Platform Engineering` -> `platform-eng`-style slug (not truncated)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug


class TeamService:
    """Team registry plus the membership lookups authorization depends on."""

    def __init__(self, directory: TeamDirectory) -> None:
        self._directory = directory

    # ---- reads ----

    def list(self) -> list[Team]:
        return self._directory.list()

    def get(self, slug: str) -> Team | None:
        return self._directory.get(slug)

    def require(self, slug: str) -> Team:
        team = self._directory.get(slug)
        if team is None:
            raise TeamNotFoundError(slug)
        return team

    def teams_for(self, identity_uid: str) -> list[Team]:
        return [t for t in self._directory.list() if t.member(identity_uid)]

    def team_role(self, identity_uid: str, slug: str) -> str | None:
        team = self._directory.get(slug)
        return team.role_of(identity_uid) if team else None

    def administered_by(self, identity_uid: str) -> list[str]:
        """Slugs of every team this identity administers."""
        return [
            t.slug
            for t in self._directory.list()
            if t.role_of(identity_uid) == "admin"
        ]

    # ---- writes ----

    def create(
        self, slug: str, name: str, description: str, created_by: str
    ) -> Team:
        slug = slug.strip().lower()
        if not SLUG_PATTERN.match(slug):
            raise ValueError(
                "Team slug must be lowercase letters, digits, and hyphens: "
                f"{slug!r}"
            )
        team = Team(
            slug=slug,
            name=name.strip() or slug,
            description=description.strip(),
            created_by=created_by,
        )
        return self._directory.create(team)

    def update_details(
        self, slug: str, name: str | None, description: str | None
    ) -> Team:
        team = self.require(slug)
        patch: dict = {}
        if name is not None and name.strip():
            patch["name"] = name.strip()
        if description is not None:
            patch["description"] = description.strip()
        return self._directory.update(team.model_copy(update=patch))

    def set_member(
        self, slug: str, identity_uid: str, team_role: str, added_by: str
    ) -> Team:
        if team_role not in ("admin", "member"):
            raise ValueError(f"Team role must be 'admin' or 'member': {team_role}")
        team = self.require(slug)
        others = [m for m in team.members if m.identity_uid != identity_uid]
        existing = team.member(identity_uid)
        member = TeamMember(
            identity_uid=identity_uid,
            team_role=team_role,  # type: ignore[arg-type]
            # Keep the original join date when only the role is changing.
            added_at=existing.added_at if existing else utcnow(),
            added_by=existing.added_by if existing else added_by,
        )
        return self._directory.update(
            team.model_copy(update={"members": others + [member]})
        )

    def remove_member(self, slug: str, identity_uid: str) -> Team:
        team = self.require(slug)
        remaining = [m for m in team.members if m.identity_uid != identity_uid]
        if len(remaining) == len(team.members):
            return team
        return self._directory.update(
            team.model_copy(update={"members": remaining})
        )

    def remove_identity_everywhere(self, identity_uid: str) -> int:
        """Drop a deleted identity from every team it belonged to."""
        removed = 0
        for team in self._directory.list():
            if team.member(identity_uid) is None:
                continue
            self.remove_member(team.slug, identity_uid)
            removed += 1
        return removed

    def delete(self, slug: str) -> Team:
        team = self.require(slug)
        self._directory.delete(slug)
        return team


def bootstrap_teams_from_assets(service: TeamService, owners: list[str]) -> int:
    """Register a team for every owner string the corpus already carries.

    `Asset.owner` predates this module and holds bare slugs like `platform-eng`.
    Treating those as team slugs turns existing data into real ownership without
    a migration; this backfills the registry so those references resolve.
    Idempotent, so later instances find nothing to do.
    """
    created = 0
    for owner in sorted({o.strip() for o in owners if o and o.strip()}):
        slug = owner if SLUG_PATTERN.match(owner) else slugify(owner)
        if not slug or service.get(slug) is not None:
            continue
        try:
            service.create(
                slug=slug,
                name=owner.replace("-", " ").title(),
                description="Imported from existing asset ownership.",
                created_by="system:team-bootstrap",
            )
            created += 1
        except (TeamExistsError, ValueError):
            continue
    return created


def build_team_directory(
    backend: str, project: str | None, collection: str
) -> TeamDirectory:
    if backend == "firestore":
        return FirestoreTeamDirectory(project, collection)
    return InMemoryTeamDirectory()
