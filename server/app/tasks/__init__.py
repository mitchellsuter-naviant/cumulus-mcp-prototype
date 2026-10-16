"""FDE-authored process packages: one directory per business process.

Each package holds the process's domain (its case models, its service, its
queue and knowledge bindings) and one ``v<N>/`` directory per shipped version
of its Flow — the executable body plus the declarative steps the map shows for
that version. The contract a version package exposes:

    VERSION: int          — the version number, matching its directory name
    LABEL: str            — the version picker's short name
    NOTES: str            — what this version changes, one paragraph
    STEPS: list[FlowStep] — the Tier-2 export of this version's shape
    UI: TaskUi            — optional; which screen answers each task kind this
                            version raises (see `app.task_screens`). A version
                            that changed nothing a person sees declares none
                            and is answered by the newest declaration below it
    build_body(service)   — binds the process's service and returns the
                            engine-runnable FlowBody

The versions the console can make live are exactly the ``v<N>/`` packages
present in the shipped code — discovery below reads the filesystem, so a
version that was not deployed cannot be activated, and one that was needs no
registration step beyond existing. Which shipped version is *live* is a stored
decision (``FlowDefinition.version``) the console flips; the code only declares
a default for environments that have never chosen.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from types import ModuleType

_VERSION_DIR = re.compile(r"^v(\d+)$")

_discovered: dict[str, dict[int, ModuleType]] | None = None
_defaults: dict[str, int] = {}


def _discover() -> dict[str, dict[int, ModuleType]]:
    """flow id → version number → the imported ``v<N>`` package.

    Scanned once and cached — the filesystem walk and the imports both: the
    packages are import-heavy, every service construction asks, and the set of
    shipped versions cannot change without a redeploy, so there is nothing to
    re-scan for.
    """
    global _discovered
    if _discovered is not None:
        return _discovered

    found: dict[str, dict[int, ModuleType]] = {}
    for pkg_info in pkgutil.iter_modules(__path__):
        if not pkg_info.ispkg:
            continue
        package = importlib.import_module(f"{__name__}.{pkg_info.name}")
        flow_id = getattr(package, "FLOW_ID", "")
        if not flow_id:
            continue
        versions: dict[int, ModuleType] = {}
        for sub in pkgutil.iter_modules(package.__path__):
            match = _VERSION_DIR.match(sub.name)
            if match is None or not sub.ispkg:
                continue
            module = importlib.import_module(f"{package.__name__}.{sub.name}")
            versions[int(match.group(1))] = module
        if versions:
            found[flow_id] = versions
            declared = int(getattr(package, "DEFAULT_VERSION", 0) or 0)
            _defaults[flow_id] = (
                declared if declared in versions else max(versions)
            )
    _discovered = found
    return found


def shipped_versions(flow_id: str) -> dict[int, ModuleType]:
    """The version packages shipped for one flow, by version number."""
    return dict(_discover().get(flow_id, {}))


def shipped_version_numbers(flow_id: str) -> set[int]:
    return set(_discover().get(flow_id, {}))


def default_version(flow_id: str) -> int:
    """The version a fresh environment runs — the package's declaration.

    Falls back to the highest shipped version for a package that declares
    none, and to 0 for a flow with no package at all.
    """
    _discover()
    return _defaults.get(flow_id, 0)


def task_flow_ids() -> list[str]:
    """Every flow id with at least one shipped version package."""
    return sorted(_discover())
