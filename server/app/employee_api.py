"""REST surface for the Employee Hub: profile, PTO, and the HR agent.

"Me" is `principal.email`, everywhere and without exception: the graph keys
an `Employee` on their email, so any other key resolves to nobody.

Everything here is scoped to that person — the hub is a personal surface, not
an admin one — so every route is user+ and human-only where it mutates. The
HR agent read path allows keys (an ambient agent asking on a person's behalf
is the MCP posture), but PTO requests are judgments about your own time and
are refused to keys.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import Principal, get_principal, require_human, require_reader
from .deps import get_employee_service
from .employee import EmployeeProfile, HrChatReply, HrTurn, PtoRequest

log = logging.getLogger("cumulus.employee.api")

router = APIRouter(prefix="/api/v1/employee", tags=["employee"])


class PtoSubmit(BaseModel):
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    days: float = Field(gt=0, le=30)
    note: str = Field(default="", max_length=500)


class HrChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    #: The conversation so far, oldest first, replayed by the client. The
    #: server keeps no session: a surface that answers from somebody's own
    #: health plan and PTO is the last place to accumulate a transcript store,
    #: and "new chat" is only honest if the history it drops was never ours.
    #: Capped so one conversation cannot grow into an unbounded prompt.
    history: list[HrTurn] = Field(default_factory=list, max_length=20)


@router.get("/me", response_model=EmployeeProfile)
def my_profile(principal: Principal = Depends(get_principal)) -> EmployeeProfile:
    require_reader(principal)
    return get_employee_service().profile(principal.email)


@router.get("/pto", response_model=list[PtoRequest])
def my_pto_requests(
    principal: Principal = Depends(get_principal),
) -> list[PtoRequest]:
    require_reader(principal)
    return get_employee_service().requests_for(principal.email)


@router.post("/pto", response_model=PtoRequest, status_code=201)
def request_pto(
    body: PtoSubmit, principal: Principal = Depends(get_principal)
) -> PtoRequest:
    require_reader(principal)
    require_human(principal, "act on an employee's behalf")
    if body.end_date < body.start_date:
        raise HTTPException(status_code=422, detail="end_date precedes start_date")
    return get_employee_service().request_pto(
        principal.email, body.start_date, body.end_date, body.days, body.note
    )


@router.post("/hr-chat", response_model=HrChatReply)
def hr_chat(
    body: HrChatMessage, principal: Principal = Depends(get_principal)
) -> HrChatReply:
    require_reader(principal)
    return get_employee_service().chat(
        principal.email, body.message, body.history
    )
