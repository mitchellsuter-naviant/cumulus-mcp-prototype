"""The ontology: the customer's canonical model, separated from the machinery.

The TBox — Object types, Link types, the claim-precedence policy, and the
governed markdown rendering of all of it — lives in `tbox`. This package
re-exports its public surface so `from app.ontology import ONTOLOGY` reads the
way the doctrine talks: the ontology is a thing of its own, not one file among
seventy.
"""

from .tbox import *  # noqa: F401,F403 — the package IS the tbox's surface
from .tbox import ONTOLOGY, Ontology  # noqa: F401 — the two everyone reaches for
