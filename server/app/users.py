"""Platform user directory: Firebase Auth in production, in-memory for local dev."""

import threading
import uuid
from typing import Protocol

from pydantic import BaseModel

from .auth import ROLES


class PlatformUser(BaseModel):
    uid: str
    email: str
    display_name: str = ""
    role: str = "user"
    disabled: bool = False


class UserExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class UserDirectory(Protocol):
    def list(self) -> list[PlatformUser]: ...
    def get(self, uid: str) -> PlatformUser | None: ...
    def create(
        self, email: str, password: str, display_name: str, role: str
    ) -> PlatformUser: ...
    def set_role(self, uid: str, role: str) -> PlatformUser: ...
    def delete(self, uid: str) -> None: ...


class InMemoryUserDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users: dict[str, PlatformUser] = {}
        # Dev convenience: a default owner account.
        owner = PlatformUser(
            uid="dev-owner",
            email="owner@localhost",
            display_name="Dev Owner",
            role="owner",
        )
        self._users[owner.uid] = owner

    def list(self) -> list[PlatformUser]:
        with self._lock:
            return sorted(self._users.values(), key=lambda u: u.email)

    def get(self, uid: str) -> PlatformUser | None:
        with self._lock:
            return self._users.get(uid)

    def create(
        self, email: str, password: str, display_name: str, role: str
    ) -> PlatformUser:
        with self._lock:
            if any(u.email == email for u in self._users.values()):
                raise UserExistsError(email)
            user = PlatformUser(
                uid=uuid.uuid4().hex[:12],
                email=email,
                display_name=display_name,
                role=role,
            )
            self._users[user.uid] = user
            return user

    def set_role(self, uid: str, role: str) -> PlatformUser:
        with self._lock:
            user = self._users.get(uid)
            if user is None:
                raise UserNotFoundError(uid)
            updated = user.model_copy(update={"role": role})
            self._users[uid] = updated
            return updated

    def delete(self, uid: str) -> None:
        with self._lock:
            if uid not in self._users:
                raise UserNotFoundError(uid)
            del self._users[uid]


class FirebaseUserDirectory:
    """Users live in Firebase Auth; the role is a `role` custom claim."""

    def __init__(self) -> None:
        from .firebase import ensure_firebase

        ensure_firebase()

    @staticmethod
    def _to_user(record) -> PlatformUser:
        claims = record.custom_claims or {}
        role = claims.get("role", "user")
        return PlatformUser(
            uid=record.uid,
            email=record.email or "",
            display_name=record.display_name or "",
            role=role if role in ROLES else "user",
            disabled=record.disabled,
        )

    def list(self) -> list[PlatformUser]:
        from firebase_admin import auth as fb_auth

        users = [self._to_user(u) for u in fb_auth.list_users().iterate_all()]
        return sorted(users, key=lambda u: u.email)

    def get(self, uid: str) -> PlatformUser | None:
        from firebase_admin import auth as fb_auth

        try:
            return self._to_user(fb_auth.get_user(uid))
        except fb_auth.UserNotFoundError:
            return None

    def create(
        self, email: str, password: str, display_name: str, role: str
    ) -> PlatformUser:
        from firebase_admin import auth as fb_auth

        try:
            record = fb_auth.create_user(
                email=email, password=password, display_name=display_name or None
            )
        except fb_auth.EmailAlreadyExistsError:
            raise UserExistsError(email)
        fb_auth.set_custom_user_claims(record.uid, {"role": role})
        return PlatformUser(
            uid=record.uid, email=email, display_name=display_name, role=role
        )

    def set_role(self, uid: str, role: str) -> PlatformUser:
        from firebase_admin import auth as fb_auth

        try:
            fb_auth.set_custom_user_claims(uid, {"role": role})
            return self._to_user(fb_auth.get_user(uid))
        except fb_auth.UserNotFoundError:
            raise UserNotFoundError(uid)

    def delete(self, uid: str) -> None:
        from firebase_admin import auth as fb_auth

        try:
            fb_auth.delete_user(uid)
        except fb_auth.UserNotFoundError:
            raise UserNotFoundError(uid)


def build_user_directory(backend: str) -> UserDirectory:
    if backend == "firebase":
        return FirebaseUserDirectory()
    return InMemoryUserDirectory()
