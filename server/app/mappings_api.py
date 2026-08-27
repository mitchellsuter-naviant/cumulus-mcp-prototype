"""REST surface for the Mapping registry.

Read-only over HTTP for the same reason the graph is: mappings are declared in
Python and exported, not edited in place. The endpoint exists so the console's
Sources view can show each feed *against its contract* — what binds to what,
under which policy — rather than as a bare row of delivery counts.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import Principal, get_principal, require_reader
from .mappings import MAPPINGS, Mapping, validate_mappings

router = APIRouter(prefix="/api/v1/mappings", tags=["mappings"])


class IdentityBindingView(BaseModel):
    column: str
    object_type: str
    key: str


class MappingView(BaseModel):
    id: str
    source: str
    system: str
    summary: str
    transport: str
    payload: str
    strategy: str
    lands_as: str
    resolves_to: list[str]
    identity: list[IdentityBindingView]
    precedence_source: str
    match_policy: str
    freshness: str
    custody: str
    collection: str
    escalation: str
    notes: str

    @classmethod
    def of(cls, m: Mapping) -> "MappingView":
        return cls(
            id=m.id, source=m.source, system=m.system, summary=m.summary,
            transport=m.transport, payload=m.payload, strategy=m.strategy,
            lands_as=m.lands_as, resolves_to=list(m.resolves_to),
            identity=[
                IdentityBindingView(
                    column=b.column, object_type=b.object_type, key=b.key
                )
                for b in m.identity
            ],
            precedence_source=m.precedence_source, match_policy=m.match_policy,
            freshness=m.freshness, custody=m.custody, collection=m.collection,
            escalation=m.escalation, notes=m.notes,
        )


class MappingRegistry(BaseModel):
    mappings: list[MappingView]
    # Same spirit as the TBox invariant sweep: a registry that names a type or
    # Collection that does not exist says so, instead of looking fine.
    problems: list[str]


@router.get("", response_model=MappingRegistry)
def list_mappings(principal: Principal = Depends(get_principal)) -> MappingRegistry:
    require_reader(principal)
    return MappingRegistry(
        mappings=[MappingView.of(m) for m in MAPPINGS],
        problems=validate_mappings(),
    )


@router.get("/{mapping_id}", response_model=MappingView)
def get_mapping(
    mapping_id: str, principal: Principal = Depends(get_principal)
) -> MappingView:
    require_reader(principal)
    for m in MAPPINGS:
        if m.id == mapping_id:
            return MappingView.of(m)
    raise HTTPException(status_code=404, detail=f"No such mapping: {mapping_id}")
