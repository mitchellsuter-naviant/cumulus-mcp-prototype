"""Publish the code-declared governed knowledge into the corpus.

The ontology and the Collection registry are authored in Python and *exported*
to the map — that is the doctrine's authoring model, and it is the only way the
document a person reads and the schema the system runs can be guaranteed not to
drift. So they are republished on every startup rather than seeded once: if the
declaration changes, the asset changes with it, and the revision counter records
that it did.

Everything else in the corpus is the opposite — authored by people, seeded once,
never overwritten by code. These two are the exception because they have a
single source of truth that is not the document.
"""

from __future__ import annotations

import logging

from . import data_collections
from .models import AssetCreate, AssetUpdate
from .eval_specs import specs_asset_fields
from .mappings import mappings_asset_fields
from .ontology import ontology_asset_fields
from .data_collections import collections_asset_fields

log = logging.getLogger("cumulus.platform_assets")


def publish_generated_assets(knowledge) -> list[str]:
    """Upsert the generated documents. Idempotent, and quiet when unchanged."""
    published: list[str] = []
    for fields in (
        ontology_asset_fields(),
        collections_asset_fields(),
        mappings_asset_fields(),
        specs_asset_fields(),
    ):
        asset_id = fields["id"]
        content = fields.pop("content")
        existing = knowledge.repo.get(asset_id)
        try:
            if existing is None:
                knowledge.create(AssetCreate(content=content, **fields))
                published.append(asset_id)
            elif existing.content != content:
                knowledge.update(asset_id, AssetUpdate(content=content))
                published.append(asset_id)
        except Exception:
            # A corpus that will not take the generated docs is a degraded
            # service, not a dead one: everything else still works.
            log.exception(
                "Could not publish generated asset", extra={"assetId": asset_id}
            )
    if published:
        log.info("Published generated governed knowledge", extra={"assets": published})
    return published
