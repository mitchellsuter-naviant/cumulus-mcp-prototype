"""Identities: the things that hold credentials.

An identity is *who or what* is acting — a person, an autonomous agent, or a
machine client. Every minted API key hangs off exactly one of them, so a key is
always attributable and an identity's authority can be changed or withdrawn in
one place.

People are not duplicated here. They already exist as Firebase Auth accounts
with a `role` custom claim (see users.py), so this module projects those
accounts into the identity list rather than keeping a second copy that would
drift. Agents and service clients have no password and cannot sign in, so they
get their own registry.
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .models import utcnow
from .users import PlatformUser, UserDirectory, UserNotFoundError

log = logging.getLogger("cumulus.identities")

IdentityKind = Literal["person", "agent", "service"]

# Kinds that live in this module's registry; `person` comes from Firebase Auth.
MACHINE_KINDS: tuple[str, ...] = ("agent", "service")

# Machine identities are capped below owner for the same reason keys are: a
# credential file should never be able to grant roles or delete accounts.
ASSIGNABLE_MACHINE_ROLES = ["user", "admin"]

# How long a resolved identity may be reused when authenticating an API key.
# Every keyed request resolves the holder's role, and without this that is a
# Firestore read (or a Firebase Auth call) on the hot path. The cost of the
# window is that a role change or a disable takes up to this long to bite.
_CACHE_TTL_SECONDS = 60.0


class Identity(BaseModel):
    uid: str
    kind: IdentityKind
    display_name: str
    email: str = ""
    role: str = "user"
    # Agents and services only: what it does, and the person accountable for it.
    description: str = ""
    owner: str = ""
    disabled: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    created_by: str = ""

    @property
    def is_person(self) -> bool:
        return self.kind == "person"

    @property
    def label(self) -> str:
        return self.display_name or self.email or self.uid


class IdentityExistsError(Exception):
    pass


class IdentityNotFoundError(Exception):
    def __init__(self, uid: str) -> None:
        super().__init__(f"Identity not found: {uid}")
        self.uid = uid


class MachineIdentityDirectory(Protocol):
    """Storage for the non-person identities."""

    def list(self) -> list[Identity]: ...
    def get(self, uid: str) -> Identity | None: ...
    def create(self, identity: Identity) -> Identity: ...
    def update(self, identity: Identity) -> Identity: ...
    def delete(self, uid: str) -> None: ...


def new_machine_uid(kind: str) -> str:
    """`agt_9f2c1a0b4d7e` — the prefix keeps kinds legible in logs and tokens."""
    prefix = "svc" if kind == "service" else "agt"
    return f"{prefix}_{secrets.token_hex(6)}"


class InMemoryMachineIdentityDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Identity] = {}

    def list(self) -> list[Identity]:
        with self._lock:
            return sorted(self._items.values(), key=lambda i: i.display_name.lower())

    def get(self, uid: str) -> Identity | None:
        with self._lock:
            return self._items.get(uid)

    def create(self, identity: Identity) -> Identity:
        with self._lock:
            if identity.uid in self._items:
                raise IdentityExistsError(identity.uid)
            if any(
                i.display_name.lower() == identity.display_name.lower()
                for i in self._items.values()
            ):
                raise IdentityExistsError(identity.display_name)
            self._items[identity.uid] = identity
            return identity

    def update(self, identity: Identity) -> Identity:
        with self._lock:
            if identity.uid not in self._items:
                raise IdentityNotFoundError(identity.uid)
            self._items[identity.uid] = identity
            return identity

    def delete(self, uid: str) -> None:
        with self._lock:
            if uid not in self._items:
                raise IdentityNotFoundError(uid)
            del self._items[uid]


class FirestoreMachineIdentityDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[Identity]:
        out = []
        for doc in self._col.stream():
            try:
                out.append(Identity.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable identity", extra={"docId": doc.id})
        return sorted(out, key=lambda i: i.display_name.lower())

    def get(self, uid: str) -> Identity | None:
        snap = self._col.document(uid).get()
        if not snap.exists:
            return None
        try:
            return Identity.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable identity document", extra={"identityUid": uid})
            return None

    def create(self, identity: Identity) -> Identity:
        ref = self._col.document(identity.uid)
        if ref.get().exists:
            raise IdentityExistsError(identity.uid)
        # Same guard as the in-memory store: two agents sharing a name makes the
        # "acts as" column in the console meaningless. Equality on one field
        # needs no composite index.
        clash = self._col.where(
            field_path="display_name", op_string="==", value=identity.display_name
        ).limit(1).get()
        if list(clash):
            raise IdentityExistsError(identity.display_name)
        ref.set(identity.model_dump(mode="json"))
        return identity

    def update(self, identity: Identity) -> Identity:
        ref = self._col.document(identity.uid)
        if not ref.get().exists:
            raise IdentityNotFoundError(identity.uid)
        ref.set(identity.model_dump(mode="json"))
        return identity

    def delete(self, uid: str) -> None:
        ref = self._col.document(uid)
        if not ref.get().exists:
            raise IdentityNotFoundError(uid)
        ref.delete()


class IdentityService:
    """One view over both stores: Firebase Auth people + machine registry."""

    def __init__(
        self, users: UserDirectory, machines: MachineIdentityDirectory
    ) -> None:
        self._users = users
        self._machines = machines
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[Identity | None, datetime]] = {}

    # ---- projection ----

    @staticmethod
    def _from_user(user: PlatformUser) -> Identity:
        return Identity(
            uid=user.uid,
            kind="person",
            display_name=user.display_name or user.email,
            email=user.email,
            role=user.role,
            disabled=user.disabled,
        )

    # ---- reads ----

    def list(self) -> list[Identity]:
        people = [self._from_user(u) for u in self._users.list()]
        return people + list(self._machines.list())

    def get(self, uid: str) -> Identity | None:
        machine = self._machines.get(uid) if _looks_machine(uid) else None
        if machine is not None:
            return machine
        user = self._users.get(uid)
        return self._from_user(user) if user is not None else None

    def get_cached(self, uid: str) -> Identity | None:
        """[get] with a short TTL, for the API-key authentication path."""
        now = utcnow()
        with self._lock:
            hit = self._cache.get(uid)
            if hit is not None and hit[1] > now:
                return hit[0]
        identity = self.get(uid)
        with self._lock:
            self._cache[uid] = (
                identity,
                now + timedelta(seconds=_CACHE_TTL_SECONDS),
            )
        return identity

    def invalidate(self, uid: str | None = None) -> None:
        with self._lock:
            if uid is None:
                self._cache.clear()
            else:
                self._cache.pop(uid, None)

    # ---- writes (machine identities; people go through the user directory) ----

    def create_machine(
        self,
        kind: str,
        display_name: str,
        role: str,
        description: str,
        owner: str,
        created_by: str,
    ) -> Identity:
        if kind not in MACHINE_KINDS:
            raise ValueError(f"Kind must be one of {list(MACHINE_KINDS)}: {kind}")
        if role not in ASSIGNABLE_MACHINE_ROLES:
            raise ValueError(
                f"Machine identity role must be one of {ASSIGNABLE_MACHINE_ROLES}: {role}"
            )
        identity = Identity(
            uid=new_machine_uid(kind),
            kind=kind,  # type: ignore[arg-type]
            display_name=display_name,
            role=role,
            description=description,
            owner=owner,
            created_by=created_by,
        )
        created = self._machines.create(identity)
        self.invalidate(created.uid)
        return created

    def set_role(self, uid: str, role: str) -> Identity:
        """Route the change to whichever store owns this identity."""
        machine = self._machines.get(uid) if _looks_machine(uid) else None
        if machine is not None:
            if role not in ASSIGNABLE_MACHINE_ROLES:
                raise ValueError(
                    f"Machine identity role must be one of {ASSIGNABLE_MACHINE_ROLES}: {role}"
                )
            updated = self._machines.update(machine.model_copy(update={"role": role}))
            self.invalidate(uid)
            return updated
        try:
            user = self._users.set_role(uid, role)
        except UserNotFoundError:
            raise IdentityNotFoundError(uid)
        self.invalidate(uid)
        return self._from_user(user)

    def delete(self, uid: str) -> Identity:
        existing = self.get(uid)
        if existing is None:
            raise IdentityNotFoundError(uid)
        if existing.is_person:
            try:
                self._users.delete(uid)
            except UserNotFoundError:
                raise IdentityNotFoundError(uid)
        else:
            self._machines.delete(uid)
        self.invalidate(uid)
        return existing


def _looks_machine(uid: str) -> bool:
    return uid.startswith(("agt_", "svc_"))


def build_machine_identity_directory(
    backend: str, project: str | None, collection: str
) -> MachineIdentityDirectory:
    if backend == "firestore":
        return FirestoreMachineIdentityDirectory(project, collection)
    return InMemoryMachineIdentityDirectory()
