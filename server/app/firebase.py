"""Firebase Admin SDK initialization (used when AUTH_BACKEND=firebase)."""

import threading

_lock = threading.Lock()
_initialized = False


def ensure_firebase() -> None:
    global _initialized
    with _lock:
        if _initialized:
            return
        import firebase_admin

        # Uses Application Default Credentials (Cloud Run service account,
        # or GOOGLE_APPLICATION_CREDENTIALS locally).
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
        _initialized = True
