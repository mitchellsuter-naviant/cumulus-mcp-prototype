"""The Employee Hub's server half: profile, PTO, benefits, and the HR agent.

The hub is deliberately built from the same primitives as everything else:

- PTO requests follow the flow pattern in miniature — a governed value (the
  auto-approval window, parsed from the `pto-policy` knowledge asset) decides
  whether a request auto-approves or raises a Task to the `hr` queue, and the
  human's decision resumes it.
- The HR agent is a grounded answerer over the knowledge layer scoped to the
  `people/` taxonomy plus the employee's own live facts (balance, requests).
  With `MODEL_BACKEND=vertex` it plans with tools; locally it degrades to
  retrieval plus the structured facts — cited either way, never invented.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import personas
from .graph import GraphService, object_id
from .models import AssetFilter, utcnow
from .tasks import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.employee")

PTO_POLICY_ASSET_ID = "pto-policy"
HR_QUEUE = "hr"

PtoState = Literal["approved", "pending", "denied"]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Benefit(BaseModel):
    name: str
    kind: str  # medical | dental | vision | retirement | perk
    summary: str
    status: str = "enrolled"


class EmployeeProfile(BaseModel):
    """Who this person is, and the state the hub keeps about them.

    The two halves have different owners, which is why only one of them is
    stored. Name, title, team, manager and location belong to the org chart,
    so they are read back out of the graph on every call rather than copied
    once and left to rot; the balance, the accrual and the benefits are the
    hub's own and are the only part it writes down.
    """

    email: str
    name: str = ""
    title: str = ""
    team: str = ""
    manager: str = ""
    location: str = ""
    #: False when no `Employee` in the org chart carries this email. The hub
    #: still renders and says why it is empty — the alternative, and what this
    #: used to do, is to invent a person nobody employs.
    in_workforce: bool = True
    workforce_note: str = ""
    pto_balance_days: float = 17.5
    pto_accrual_per_month: float = 1.67
    next_company_holiday: str = "Labor Day · Sep 7"
    benefits: list[Benefit] = Field(default_factory=list)


class PtoRequest(BaseModel):
    id: str
    email: str
    start_date: str
    end_date: str
    days: float
    note: str = ""
    state: PtoState = "pending"
    reason: str = ""
    task_id: str = ""
    decided_by: str = ""
    created_at_iso: str = ""


class HrChatReply(BaseModel):
    reply: str
    citations: list[dict] = Field(default_factory=list)
    grounded_on: list[str] = Field(default_factory=list)
    backend: str = "retrieval"


def _default_benefits() -> list[Benefit]:
    return [
        Benefit(
            name="Health PPO 2500",
            kind="medical",
            summary="Employer covers 85% of premium; $2,500 deductible; "
                    "HSA-eligible with a $750 annual employer contribution.",
        ),
        Benefit(
            name="Dental + Vision",
            kind="dental",
            summary="Preventive covered 100%; two cleanings a year; "
                    "$200 annual vision allowance.",
        ),
        Benefit(
            name="401(k) with match",
            kind="retirement",
            summary="4% employer match, vested immediately; enrollment "
                    "changes take effect the following pay period.",
        ),
        Benefit(
            name="Learning stipend",
            kind="perk",
            summary="$1,200/year for courses, books, and conferences — "
                    "submit through reimbursements with category 'software' "
                    "or 'supplies'.",
        ),
    ]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class EmployeeDirectory(Protocol):
    def get_profile(self, email: str) -> EmployeeProfile | None: ...
    def put_profile(self, profile: EmployeeProfile) -> EmployeeProfile: ...
    def list_requests(self) -> list[PtoRequest]: ...
    def get_request(self, request_id: str) -> PtoRequest | None: ...
    def put_request(self, request: PtoRequest) -> PtoRequest: ...


class InMemoryEmployeeDirectory:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._profiles: dict[str, EmployeeProfile] = {}
        self._requests: dict[str, PtoRequest] = {}

    def get_profile(self, email: str) -> EmployeeProfile | None:
        with self._lock:
            return self._profiles.get(email)

    def put_profile(self, profile: EmployeeProfile) -> EmployeeProfile:
        with self._lock:
            self._profiles[profile.email] = profile
            return profile

    def list_requests(self) -> list[PtoRequest]:
        with self._lock:
            return list(self._requests.values())

    def get_request(self, request_id: str) -> PtoRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def put_request(self, request: PtoRequest) -> PtoRequest:
        with self._lock:
            self._requests[request.id] = request
            return request


class FirestoreEmployeeDirectory:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore

        self._client = firestore.Client(project=project)
        self._profiles = self._client.collection(f"{collection}_profiles")
        self._requests = self._client.collection(f"{collection}_pto")

    def get_profile(self, email: str) -> EmployeeProfile | None:
        snap = self._profiles.document(email).get()
        if not snap.exists:
            return None
        try:
            return EmployeeProfile.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable profile", extra={"email": email})
            return None

    def put_profile(self, profile: EmployeeProfile) -> EmployeeProfile:
        self._profiles.document(profile.email).set(profile.model_dump(mode="json"))
        return profile

    def list_requests(self) -> list[PtoRequest]:
        out = []
        for doc in self._requests.stream():
            try:
                out.append(PtoRequest.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable PTO request",
                              extra={"docId": doc.id})
        return out

    def get_request(self, request_id: str) -> PtoRequest | None:
        snap = self._requests.document(request_id).get()
        if not snap.exists:
            return None
        try:
            return PtoRequest.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable PTO request", extra={"id": request_id})
            return None

    def put_request(self, request: PtoRequest) -> PtoRequest:
        self._requests.document(request.id).set(request.model_dump(mode="json"))
        return request


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def parse_auto_approve_days(content: str) -> float:
    """The governed value: 'requests of N working days or fewer auto-approve'."""
    import re

    match = re.search(
        r"(\d+(?:\.\d+)?)\s+working days or fewer", content, re.IGNORECASE
    )
    return float(match.group(1)) if match else 3.0


class EmployeeService:
    def __init__(
        self,
        directory: EmployeeDirectory,
        knowledge,  # KnowledgeService — duck-typed to avoid an import cycle
        tasks: TaskService,
        graph: GraphService,
        answer_backend=None,
        teams=None,
    ) -> None:
        self.directory = directory
        self.knowledge = knowledge
        self.tasks = tasks
        self.graph = graph
        self.answer_backend = answer_backend
        self.teams = teams

    # -- profile --------------------------------------------------------------

    def profile(self, email: str) -> EmployeeProfile:
        """The hub's view of one person, keyed the way the graph keys them.

        A principal the org chart has never heard of gets an empty profile
        that says so. It used to get a freshly minted one instead, complete
        with a default balance and a full set of benefits — so the hub showed
        a person nobody employs and days nobody had accrued.
        """
        person = self.graph.get(object_id("Employee", email)) if email else None
        if person is None:
            return EmployeeProfile(
                email=email,
                in_workforce=False,
                workforce_note=(
                    f"{email or 'This principal'} is not in the workforce, so "
                    "there is no balance, manager or enrollment to show."
                ),
                pto_balance_days=0.0,
                pto_accrual_per_month=0.0,
                next_company_holiday="",
            )
        profile = self.directory.get_profile(email) or self.directory.put_profile(
            EmployeeProfile(email=email, benefits=_default_benefits())
        )
        profile.name = str(person.props.get("name") or person.label)
        profile.title = str(person.props.get("title") or "")
        profile.location = str(person.props.get("location") or "")
        profile.team = self._linked_label(person.id, "member_of")
        profile.manager = self._linked_label(person.id, "reports_to")
        return profile

    def _linked_label(self, person_id: str, link_type: str) -> str:
        for _, other in self.graph.neighbors(person_id, link_type, "out"):
            return other.label
        return ""

    def requests_for(self, email: str) -> list[PtoRequest]:
        return sorted(
            (r for r in self.directory.list_requests() if r.email == email),
            key=lambda r: r.created_at_iso,
            reverse=True,
        )

    # -- PTO: the flow pattern in miniature ------------------------------------

    def auto_approve_window(self) -> tuple[float, str]:
        """The governed window, plus the revision-stamped ref that was read."""
        asset = self.knowledge.repo.get(PTO_POLICY_ASSET_ID)
        if asset is None:
            return 3.0, f"knowledge:{PTO_POLICY_ASSET_ID}"
        ref = f"knowledge:{PTO_POLICY_ASSET_ID}@rev{getattr(asset, 'revision', 1)}"
        return parse_auto_approve_days(asset.content), ref

    def request_pto(
        self, email: str, start_date: str, end_date: str, days: float, note: str = ""
    ) -> PtoRequest:
        profile = self.profile(email)
        window, policy_ref = self.auto_approve_window()
        rid = f"pto_{email}_{start_date.replace('-', '')}"
        request = PtoRequest(
            id=rid, email=email, start_date=start_date, end_date=end_date,
            days=days, note=note, created_at_iso=utcnow().isoformat(),
        )

        if days > profile.pto_balance_days:
            request.state = "denied"
            request.reason = (
                f"{days:g} days requested against a balance of "
                f"{profile.pto_balance_days:g}"
            )
        elif days <= window:
            request.state = "approved"
            request.reason = (
                f"within the governed {window:g}-day auto-approval window"
            )
            profile.pto_balance_days = round(profile.pto_balance_days - days, 2)
            self.directory.put_profile(profile)
        else:
            request.state = "pending"
            request.reason = (
                f"over the governed {window:g}-day window; routed to HR"
            )
            task = self.tasks.create(Task(
                id=f"task_{rid}",
                kind="decision",
                title=f"PTO request: {days:g} days ({start_date} → {end_date})",
                summary=(
                    f"Over the governed {window:g}-day auto-approval window, "
                    "so the request routes to HR. "
                    f"Balance {profile.pto_balance_days:g} days covers it."
                ),
                queue=HR_QUEUE,
                case_ref=rid,
                refs=[policy_ref],
                evidence=[
                    TaskEvidence(label="Employee", value=profile.name or email),
                    TaskEvidence(label="Dates",
                                 value=f"{start_date} → {end_date} · {days:g} days"),
                    TaskEvidence(label="Balance",
                                 value=f"{profile.pto_balance_days:g} days"),
                    TaskEvidence(label="Auto-approval window",
                                 value=f"≤ {window:g} days (governed)"),
                    TaskEvidence(label="Note", value=note or "—"),
                ],
                proposal=TaskProposal(
                    action="approve-pto",
                    target="hr-system",
                    summary=f"Approve and deduct {days:g} days from the balance",
                ),
                resumes="balance deducted; calendar hold confirmed",
                created_at_iso=utcnow().isoformat(),
            ))
            request.task_id = task.id

        self.directory.put_request(request)
        log.info(
            "PTO requested",
            extra={"ptoId": rid, "state": request.state, "days": days,
                   "actor": email},
        )
        return request

    def apply_task_decision(self, task: Task) -> PtoRequest | None:
        if not task.case_ref.startswith("pto_") or task.decision is None:
            return None
        request = self.directory.get_request(task.case_ref)
        if request is None or request.state != "pending":
            return None
        approved = task.decision.verdict in ("approved", "corrected")
        request.state = "approved" if approved else "denied"
        request.decided_by = task.decision.decided_by
        request.reason = (
            f"{task.decision.verdict} by {task.decision.decided_by}"
            + (f" — {task.decision.note}" if task.decision.note else "")
        )
        if approved:
            profile = self.profile(request.email)
            profile.pto_balance_days = round(
                profile.pto_balance_days - request.days, 2
            )
            self.directory.put_profile(profile)
        self.directory.put_request(request)
        log.info(
            "PTO decided",
            extra={"ptoId": request.id, "state": request.state,
                   "actor": task.decision.decided_by},
        )
        return request

    # -- the HR agent -----------------------------------------------------------

    def _employee_facts(self, email: str) -> list[str]:
        profile = self.profile(email)
        facts = [
            f"PTO balance: {profile.pto_balance_days:g} days "
            f"(accruing {profile.pto_accrual_per_month:g}/month)",
            f"Next company holiday: {profile.next_company_holiday}",
        ]
        pending = [r for r in self.requests_for(email) if r.state == "pending"]
        if pending:
            facts.append(
                "Pending PTO requests: "
                + "; ".join(f"{r.days:g} days from {r.start_date}" for r in pending)
            )
        facts.extend(
            f"Benefit — {b.name}: {b.summary}" for b in profile.benefits
        )
        return facts

    def chat(self, email: str, message: str) -> HrChatReply:
        """Grounded HR answer: the employee's own facts + cited HR knowledge."""
        facts = self._employee_facts(email)
        hits = self.knowledge.search(
            message, limit=4, filters=AssetFilter(path_prefix="people/")
        )
        # Widen once if the people/ taxonomy has nothing for this question.
        if not hits:
            hits = self.knowledge.search(message, limit=3)
        citations = [
            {"id": h.asset.id, "title": h.asset.title,
             "authority_level": h.asset.authority_level}
            for h in hits
        ]

        if self.answer_backend is not None:
            from .answering import Toolbox

            try:
                context = (
                    "You are the HR Agent inside the Cumulus Employee Hub. "
                    "Answer only from the employee facts below and the "
                    "documents you retrieve with your tools; cite document "
                    "ids in square brackets like [pto-policy]. If neither "
                    "covers the question, say so and name who to ask.\n\n"
                    "Employee facts:\n- " + "\n- ".join(facts)
                )
                toolbox = Toolbox(self.knowledge, self.teams)
                reply, _steps = self.answer_backend.answer(
                    message, context, toolbox
                )
                cited = [
                    {"id": aid, "title": title}
                    for aid, title in toolbox.touched.items()
                ] or citations
                return HrChatReply(
                    reply=reply, citations=cited, grounded_on=facts,
                    backend=self.answer_backend.name,
                )
            except Exception:
                log.exception("HR answer backend failed; degrading to retrieval")

        # Degraded mode: structured facts + retrieval, honestly labelled.
        lines = []
        lowered = message.lower()
        if any(w in lowered for w in ("pto", "time off", "vacation", "holiday",
                                      "balance", "days off")):
            lines.append(facts[0] + ".")
            lines.append(f"{facts[1]}.")
        if any(w in lowered for w in ("benefit", "medical", "health", "dental",
                                      "vision", "401", "retirement", "stipend")):
            profile = self.profile(email)
            lines.extend(f"{b.name}: {b.summary}" for b in profile.benefits)
        if hits:
            top = hits[0].asset
            lines.append(
                f"The governing document is “{top.title}” "
                f"({top.authority_level}) — the citations below have the "
                "full detail."
            )
        if not lines:
            lines.append(
                "I could not find that in the HR knowledge or your records. "
                "The People team owns this — ask in #people-ops."
            )
        return HrChatReply(
            reply="\n\n".join(lines), citations=citations, grounded_on=facts,
            backend="retrieval",
        )


def build_employee_service(
    backend: str,
    project: str | None,
    collection: str,
    knowledge,
    tasks: TaskService,
    graph: GraphService,
    answer_backend=None,
    teams=None,
) -> EmployeeService:
    directory: EmployeeDirectory
    if backend == "firestore":
        directory = FirestoreEmployeeDirectory(project, collection)
    else:
        directory = InMemoryEmployeeDirectory()
    return EmployeeService(
        directory, knowledge, tasks, graph, answer_backend, teams
    )


def seed_employee_if_empty(service: EmployeeService) -> int:
    """One pending PTO request paired with the seeded task_pto_seed_01."""
    if service.directory.get_request("pto_seed_01") is not None:
        return 0
    service.directory.put_request(PtoRequest(
        id="pto_seed_01", email=personas.DEFAULT_PERSONA.email,
        start_date="2026-10-12", end_date="2026-10-20", days=7,
        note="Family trip", state="pending",
        reason="over the governed 3-day window; routed to HR",
        task_id="task_pto_seed_01",
        created_at_iso=(utcnow() - timedelta(hours=20)).isoformat(),
    ))
    return 1
