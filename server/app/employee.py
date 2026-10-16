"""The Employee Hub's server half: profile, PTO, benefits, and the HR agent.

The hub is deliberately built from the same primitives as everything else:

- PTO requests follow the flow pattern in miniature — a governed value (the
  auto-approval window, parsed from the `pto-policy` knowledge asset) decides
  whether a request auto-approves or raises a Task to the `hr` queue, and the
  human's decision resumes it.
- The HR agent is a grounded answerer over the whole governed corpus (the
  `people/` desk gets a one-place tiebreak, not a cage) plus whatever of the
  employee's own record actually exists — their placement in the org chart,
  their open requests, and a balance only if something delivered one. With
  `MODEL_BACKEND=vertex` it plans with tools; locally it degrades to retrieval
  plus those facts — cited either way, never invented.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from . import desks, personas
from .graph import GraphService, object_id
from .models import AskAnswer, AssetFilter, utcnow
from .ontology import names as onto
from .task_queue import Task, TaskEvidence, TaskProposal, TaskService

log = logging.getLogger("cumulus.employee")

PTO_POLICY_ASSET_ID = "pto-policy"
HR_QUEUE = desks.HR

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

    **Nothing here is defaulted to a plausible number.** The balance, the
    accrual, the next holiday and the enrollments used to carry class defaults
    — 17.5 days, 1.67 a month, "Labor Day · Sep 7", and four named plans with
    dollar figures on them — and every one of them was then quoted to the
    employee as their own record, by the hub and by the HR agent alike. An
    HRIS is the source for all four, and until one delivers them the honest
    value is nothing, said out loud in [records_note].
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
    #: `None` means *no balance is on record*, which is not the same fact as a
    #: balance of zero and must never render as one. A number here came from a
    #: stored profile: an HRIS delivery, or this hub's own deduction against
    #: one it was given.
    pto_balance_days: float | None = None
    pto_accrual_per_month: float | None = None
    next_company_holiday: str = ""
    benefits: list[Benefit] = Field(default_factory=list)
    #: Why the personal half is empty, for a person the org chart does know.
    #: The workforce note above answers the other case.
    records_note: str = ""
    #: The governed auto-approval window and the revision-stamped ref it was
    #: parsed from — the same pair `request_pto` decides on. Derived on every
    #: read like name and title above, never stored: the console printed a
    #: hardcoded "3 working days" beside the claim that the number was
    #: governed, so editing the policy moved the rule and not the sentence.
    pto_auto_approve_days: float = 0.0
    pto_policy_ref: str = ""


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


class HrTurn(BaseModel):
    """One turn of the conversation so far, as the console holds it.

    The client owns the transcript and replays it, rather than the server
    keeping a session: a chat that answers from an employee's own records is
    the last place to grow a store of who asked what about their health plan,
    and a hub that can start a new conversation is a hub whose history is
    disposable by construction.
    """

    role: Literal["employee", "agent"]
    text: str = Field(max_length=4000)


class HrGrounding(BaseModel):
    """What the answer was allowed to stand on, and under which rule.

    Returned rather than kept, because "the governing document is X" is a
    claim about precedence and a reader has no way to check it otherwise.
    """

    #: `knowledge:retrieval-authority@revN` — the precedence this ranked under.
    policy_ref: str = ""
    #: `policy > standard > guideline > how-to > note`.
    precedence: str = ""
    #: The coverage a document had to clear to be citable at all.
    min_coverage: float = 0.0
    #: How many retrieved documents fell below it and were dropped.
    below_floor: int = 0
    #: The query retrieval actually ran, which is not always the message —
    #: a follow-up carries the turn before it.
    query: str = ""


class HrChatReply(BaseModel):
    reply: str
    citations: list[dict] = Field(default_factory=list)
    grounded_on: list[str] = Field(default_factory=list)
    backend: str = "retrieval"
    grounding: HrGrounding = Field(default_factory=HrGrounding)


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
        teams=None,
        dispatcher=None,
    ) -> None:
        self.directory = directory
        self.knowledge = knowledge
        self.tasks = tasks
        self.graph = graph
        self.teams = teams
        # The hub used to hold an answer backend and call it directly, which
        # is what made the HR agent an ungoverned ModelCall: a planner ran, a
        # bill accrued, and no run recorded either. It emits a fact now and the
        # `answer-question` Flow owns the call — one loop, one journal, one
        # place the cost lands, whichever door the question came through.
        self.dispatcher = dispatcher

    # -- profile --------------------------------------------------------------

    #: What the hub says instead of a number nobody delivered. One sentence,
    #: because the reader's question is "why is this empty" and the answer is
    #: not "an error" — it is that this deployment has no HRIS feed for the
    #: personal half yet.
    NO_RECORDS_NOTE = (
        "No PTO balance, accrual or benefits enrollment is on record for you. "
        "An HRIS is the source for those and none has delivered them here, so "
        "the hub shows nothing rather than a deployment default."
    )

    def profile(self, email: str) -> EmployeeProfile:
        """The hub's view of one person, keyed the way the graph keys them.

        Two kinds of empty, and they are different facts. A principal the org
        chart has never heard of gets a profile that says *that* — it used to
        get a freshly minted one instead, so the hub showed a person nobody
        employs. Someone the org chart does know, with no stored profile
        behind them, gets their placement read out of the graph and
        [NO_RECORDS_NOTE] where the balance and the enrollments would be: those
        used to be minted at a class default and then quoted back as this
        person's own accrued record.

        Nothing is written on a read either. The mint-on-read that used to sit
        here put a row of invented numbers in the store the first time anybody
        opened the page.
        """
        window, policy_ref = self.auto_approve_window()
        person = self.graph.get(object_id(onto.EMPLOYEE, email)) if email else None
        if person is None:
            return EmployeeProfile(
                email=email,
                in_workforce=False,
                workforce_note=(
                    f"{email or 'This principal'} is not in the workforce, so "
                    "there is no balance, manager or enrollment to show."
                ),
                pto_auto_approve_days=window,
                pto_policy_ref=policy_ref,
            )
        profile = self.directory.get_profile(email) or EmployeeProfile(email=email)
        profile.name = str(person.props.get("name") or person.label)
        profile.title = str(person.props.get("title") or "")
        profile.location = str(person.props.get("location") or "")
        profile.team = self._linked_label(person.id, "member_of")
        profile.manager = self._linked_label(person.id, "reports_to")
        profile.records_note = (
            ""
            if profile.pto_balance_days is not None or profile.benefits
            else self.NO_RECORDS_NOTE
        )
        # The rule the hub prints, read off the same asset `request_pto` reads.
        profile.pto_auto_approve_days = window
        profile.pto_policy_ref = policy_ref
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
        """Decide one request on the governed window, and on a balance if there
        is one.

        The balance gate is real machinery over a fact that may be absent: with
        no HRIS balance on record there is nothing to check the request
        *against*, and inventing one to check it against was how a deployment
        default came to deny somebody's leave. So the window still decides, the
        request still routes, and both the reason and the Task say plainly that
        coverage was not checked.
        """
        profile = self.profile(email)
        window, policy_ref = self.auto_approve_window()
        balance = profile.pto_balance_days
        rid = f"pto_{email}_{start_date.replace('-', '')}"
        request = PtoRequest(
            id=rid, email=email, start_date=start_date, end_date=end_date,
            days=days, note=note, created_at_iso=utcnow().isoformat(),
        )

        if balance is not None and days > balance:
            request.state = "denied"
            request.reason = (
                f"{days:g} days requested against a balance of {balance:g}"
            )
        elif days <= window:
            request.state = "approved"
            request.reason = (
                f"within the governed {window:g}-day auto-approval window"
                + ("" if balance is not None else "; no balance on record to "
                   "deduct from")
            )
            if balance is not None:
                profile.pto_balance_days = round(balance - days, 2)
                self.directory.put_profile(profile)
        else:
            coverage = (
                f"Balance {balance:g} days covers it."
                if balance is not None
                else "No balance is on record, so coverage was not checked."
            )
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
                    f"so the request routes to HR. {coverage}"
                ),
                queue=HR_QUEUE,
                case_ref=rid,
                refs=[policy_ref],
                evidence=[
                    TaskEvidence(label="Employee", value=profile.name or email),
                    TaskEvidence(label="Dates",
                                 value=f"{start_date} → {end_date} · {days:g} days"),
                    TaskEvidence(
                        label="Balance",
                        value=(f"{balance:g} days" if balance is not None
                               else "not on record"),
                    ),
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
            # Nothing to deduct from when no balance was ever on record. The
            # decision still stands — a human approved the leave — and the hub
            # says why no number moved rather than writing one that came from
            # subtracting the days from a default.
            if profile.pto_balance_days is not None:
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
    #
    # Two things were wrong with this agent and they compounded.
    #
    # It searched `people/` and nothing else, so "how do I get reimbursed for a
    # flight" could not reach the travel guideline or the expense policy — the
    # two documents that answer it — while "what is our expense approval
    # policy" reached the onboarding checklist, which contains both words and
    # answers nothing. And it named `hits[0]` "the governing document"
    # unconditionally, so a ranking's best-of-a-bad-set was reported to an
    # employee as the rule. Nonsense got a governing document.
    #
    # So: the whole corpus of documents, an HR-desk tiebreak rather than a
    # cage, the governed coverage floor between "retrieved" and "citable", and
    # the governed precedence deciding which survivor governs.

    #: How many places an HR document buys over an equally-relevant one from
    #: another desk. One: enough to settle a tie, never enough to answer a
    #: question about expenses out of the benefits guide.
    HR_DESK_LIFT = 1
    HR_PATH_PREFIX = "people/"

    #: The one plane an employee's agent does not read.
    #:
    #: Not a second cage — `finance/`, `engineering/` and every other customer
    #: desk stay in reach, which was the whole point of removing the first one.
    #: `platform/` is the deployment's description of *itself*: the ontology
    #: export, the source mappings, the model price sheet, the entity-
    #: resolution policy, and the retrieval-authority document that decides
    #: this agent's own ranking. An employee asking how their dental plan works
    #: is not asking how Cumulus ranks documents, and those two questions share
    #: enough vocabulary — "coverage", "policy", "resolution" — that a
    #: governance memo about retrieval came within a tie of being named the
    #: governing document for dental cover.
    PLATFORM_PREFIX = "platform/"

    #: Below this many meaningful words, a message is read as a follow-up
    #: ("what about dental?") and the turn before it joins the query. Three,
    #: because two content words is where a question stops standing alone.
    FOLLOW_UP_TERMS = 3

    def _employee_facts(self, email: str) -> list[str]:
        """This person's own record, as sentences an answerer can stand on.

        Every line here is something stored or read, and a line is absent
        rather than filled in. Two ways this went wrong and both are closed:
        the list used to open with "PTO balance: 0 days" and "Next company
        holiday: ." for a principal the org chart had never heard of — two
        facts about nobody, and most of what made the hub read as broken — and
        for everybody else it opened with a class default that no HRIS had
        delivered, handed to a planner as the employee's own accrued balance.
        """
        profile = self.profile(email)
        if not profile.in_workforce:
            return []
        facts: list[str] = []
        if profile.pto_balance_days is not None:
            accrual = (
                f" (accruing {profile.pto_accrual_per_month:g}/month)"
                if profile.pto_accrual_per_month is not None
                else ""
            )
            facts.append(f"PTO balance: {profile.pto_balance_days:g} days{accrual}")
        if profile.next_company_holiday:
            facts.append(f"Next company holiday: {profile.next_company_holiday}")
        # Read through from the org chart on every call, like the profile is.
        # "Who do I ask about this" is the most common HR question there is and
        # the graph has the answer; the agent used to have no idea who it was
        # talking to beyond a balance.
        placement = [
            f"Title: {profile.title}" if profile.title else "",
            f"Team: {profile.team}" if profile.team else "",
            f"Manager: {profile.manager}" if profile.manager else "",
            f"Location: {profile.location}" if profile.location else "",
        ]
        facts.extend(p for p in placement if p)
        pending = [r for r in self.requests_for(email) if r.state == "pending"]
        if pending:
            facts.append(
                "Pending PTO requests: "
                + "; ".join(f"{r.days:g} days from {r.start_date}" for r in pending)
            )
        window, policy_ref = self.auto_approve_window()
        facts.append(
            f"PTO auto-approval window: {window:g} working days or fewer, "
            f"governed by {policy_ref}"
        )
        facts.extend(
            f"Benefit — {b.name}: {b.summary}" for b in profile.benefits
        )
        # An absence, stated. A planner told only what exists will fill the gap
        # with something plausible; told that the balance is not on record, it
        # can say so and name who holds it.
        if profile.records_note:
            facts.append(profile.records_note)
        return facts

    def _hr_context(
        self, email: str, facts: list[str], history: list[HrTurn]
    ) -> str:
        """What makes this the HR desk rather than the corpus front door.

        The same planner, the same tools, the same Flow — and an employee's own
        balance, placement and enrollments prepended, which is the whole of the
        difference and the reason `HrQuestionAsked` is its own event name.

        The transcript rides along for the obvious reason: "what about dental?"
        is not a question, and a planner handed it alone will answer some other
        one confidently.
        """
        policy = self.authority_policy()
        parts = [
            "You are the HR Agent inside the Cumulus Employee Hub. "
            "Answer only from the employee facts below and the documents you "
            "retrieve with your tools; cite document ids in square brackets "
            "like [pto-policy]. If neither covers the question, say so and "
            "name who to ask.\n"
            "Search the whole corpus, not just the people/ taxonomy: travel, "
            "expenses and equipment are employee questions filed under other "
            "desks. When two documents disagree, "
            f"{policy.describe()} decides which governs, and say that it did."
        ]
        if facts:
            parts.append("Employee facts:\n- " + "\n- ".join(facts))
            # The other half of the knowledge layer. "What have I spent on
            # travel this quarter" is not written in any document — it is in
            # the edges — so the planner is handed the root to walk from and
            # the shape of the walk. Without this it searches for prose that
            # does not exist and reports that the corpus does not cover it.
            root = object_id(onto.EMPLOYEE, email)
            parts.append(
                "You are also connected to the entity graph, and questions "
                "about this person's own records live there rather than in "
                "any document. Their canonical entity is "
                f"`{root}`. Walk it with `aggregate_edges` — "
                "`submitted:out>has_expense_line:out` reaches their expense "
                "lines (sum `amount_usd`, group by `category`) — and describe "
                "it with `describe_entity` for team, manager and location. "
                "Report a traversal as the path you walked, the way you would "
                "report a citation."
            )
        else:
            parts.append(
                "This principal is not on the customer's org chart, so there "
                "are no personal records to answer from. Answer policy "
                "questions from the corpus and say plainly that the personal "
                "half does not apply to this account."
            )
        if history:
            parts.append(
                "The conversation so far, oldest first:\n"
                + "\n".join(
                    f"{'Employee' if t.role == 'employee' else 'You'}: {t.text}"
                    for t in history
                )
            )
        return "\n\n".join(parts)

    def authority_policy(self):
        """The governed precedence the agent ranks and resolves conflicts by."""
        from .authority import read_authority_policy

        return read_authority_policy(self.knowledge)

    def _retrieval_query(self, message: str, history: list[HrTurn]) -> str:
        """The text retrieval actually runs.

        A follow-up is not a query. "What about dental?" shares no content word
        with the benefits guide's title and one with its body, so on its own it
        retrieves whatever else says "dental" — and with no generative backend
        there is nothing else to resolve the reference. Carrying the previous
        employee turn is the cheap half of coreference and it is honest: the
        reply reports the query it ran.
        """
        from .search import query_term_groups

        # Groups, not terms: "401k" is one thing asked about that expands to
        # two spellings, and counting the spellings would read "what about
        # 401k?" as a standalone question and drop the context that makes it
        # one.
        if len(query_term_groups(message)) >= self.FOLLOW_UP_TERMS:
            return message
        previous = next(
            (t.text for t in reversed(history) if t.role == "employee"), ""
        )
        return f"{previous} {message}".strip() if previous else message

    def _retrieve(self, query: str) -> tuple[list, int, object]:
        """Documents that clear the governed floor, best-governing first.

        Returns the survivors, how many were dropped, and the policy they were
        judged under — the caller reports all three, because an answer that
        cannot say what it discarded cannot be checked.
        """
        policy = self.authority_policy()
        # Documents only. A prompt, a config and a generated schema export are
        # machinery: they carry no authority level by construction and are not
        # an answer to a question about how the organization works. The agent
        # used to cite `source-mappings` at employees.
        hits = self.knowledge.search(
            query, limit=10, filters=AssetFilter(kind="document")
        )
        candidates = [
            h for h in hits
            if not h.asset.path.startswith(self.PLATFORM_PREFIX)
        ]
        # `grounding`, not `coverage`: on a hybrid deployment the document
        # that answers "how do I get money back for a flight" is the guideline
        # that says *reimbursement* and *airfare*, which shares no word with
        # the question. A floor read off the lexical number alone would drop
        # the hits embeddings were added to catch.
        strong = [h for h in candidates if h.grounding >= policy.min_coverage]
        dropped = len(candidates) - len(strong)

        # The HR desk's tiebreak. A stable single-place lift, so an equally
        # relevant `people/` document wins the tie and nothing else changes —
        # this is a desk preference, not the cage it replaced.
        lifted = sorted(
            (
                (
                    position - (
                        self.HR_DESK_LIFT
                        if hit.asset.path.startswith(self.HR_PATH_PREFIX)
                        else 0
                    ),
                    position,
                    hit,
                )
                for position, hit in enumerate(strong)
            ),
            key=lambda row: (row[0], row[1]),
        )
        return [hit for _, _, hit in lifted][:4], dropped, policy

    def _citation(self, hit) -> dict:
        return {
            "id": hit.asset.id,
            "title": hit.asset.title,
            "path": hit.asset.path,
            "authority_level": hit.asset.authority_level,
            "status": hit.asset.status,
            "coverage": hit.coverage,
            "semantic_strength": hit.semantic_strength,
            "grounding": hit.grounding,
            "snippet": hit.snippet,
        }

    def chat(
        self, email: str, message: str, history: list[HrTurn] | None = None
    ) -> HrChatReply:
        """Grounded HR answer: the employee's own facts + cited HR knowledge."""
        history = list(history or [])
        facts = self._employee_facts(email)
        query = self._retrieval_query(message, history)
        hits, dropped, policy = self._retrieve(query)
        citations = [self._citation(h) for h in hits]
        grounding = HrGrounding(
            policy_ref=policy.source_ref,
            precedence=policy.describe(),
            min_coverage=policy.min_coverage,
            below_floor=dropped,
            query=query,
        )

        # The fact is that somebody asked HR something. Emitted whether or not
        # a generative backend exists, because a question asked is a question
        # asked: with none configured the run still happens, records no model
        # call and no cost, and the reply below is the honest degraded one.
        answer = None
        if self.dispatcher is not None:
            # Deferred like the `Toolbox` import it replaces: the hub is
            # reachable from the answering stack's own wiring, and this module
            # has stayed importable on its own.
            from .answering import HR_QUESTION_EVENT

            try:
                run = self.dispatcher.emit(
                    HR_QUESTION_EVENT,
                    {
                        "question": message,
                        "context": self._hr_context(email, facts, history),
                        "event": HR_QUESTION_EVENT,
                    },
                    actor=email,
                ).one()
                produced = run.outputs.get("answer")
                if produced:
                    answer = AskAnswer.model_validate(produced)
            except Exception:
                log.exception("HR answer flow failed; degrading to retrieval")

        if answer is not None and answer.backend != "retrieval" and answer.answer:
            cited = [
                {"id": c.asset_id, "title": c.title, "path": c.path,
                 "authority_level": c.authority_level, "status": c.status,
                 "snippet": c.snippet}
                for c in answer.citations
            ] or citations
            return HrChatReply(
                reply=answer.answer, citations=cited, grounded_on=facts,
                backend=answer.backend, grounding=grounding,
            )

        return HrChatReply(
            reply=self._degraded_reply(email, message, facts, hits, policy),
            citations=citations, grounded_on=facts, backend="retrieval",
            grounding=grounding,
        )

    def _degraded_reply(
        self, email: str, message: str, facts: list[str], hits: list, policy
    ) -> str:
        """Structured facts plus retrieval, and nothing claimed beyond them.

        No generative backend means no prose that was not written by a person,
        so this reads out the employee's own record where the question touches
        it and hands over the documents where it does not. What it must never
        do — and what it did — is name the top of a ranking as the governing
        document when nothing in the ranking was about the question.
        """
        lines: list[str] = []
        lowered = message.lower()
        profile = self.profile(email)
        # Tracked apart from `lines`, because the not-in-the-workforce note is
        # a line and is emphatically not a personal record — without the
        # distinction the closing sentence told an operator that a reply with
        # no citations and no facts in it "is from your own record".
        from_records = False

        if not profile.in_workforce:
            lines.append(
                f"{profile.workforce_note} I can still answer from the "
                "governed policies below."
            )
        else:
            if any(w in lowered for w in ("pto", "time off", "vacation",
                                          "holiday", "balance", "days off",
                                          "leave")):
                # Read off the profile rather than off `facts[0]` and
                # `facts[1]`: those positions held a balance and a holiday
                # unconditionally, because both were defaulted. A fact that
                # may be absent cannot be reached for by index.
                if profile.pto_balance_days is not None:
                    accrual = (
                        f", accruing {profile.pto_accrual_per_month:g}/month"
                        if profile.pto_accrual_per_month is not None
                        else ""
                    )
                    lines.append(
                        f"Your PTO balance is {profile.pto_balance_days:g} days"
                        f"{accrual}."
                    )
                    from_records = True
                else:
                    lines.append(profile.records_note)
                if profile.next_company_holiday:
                    lines.append(
                        f"Next company holiday: {profile.next_company_holiday}."
                    )
                    from_records = True
                window, _ = self.auto_approve_window()
                # A fact about the deployment, not about the reader — so it is
                # stated either way and never counts as their own record.
                lines.append(
                    f"Requests of {window:g} working days or fewer auto-approve "
                    "when your balance covers them; longer ones route to HR."
                )
            if any(w in lowered for w in ("benefit", "medical", "health",
                                          "dental", "vision", "401", "retire",
                                          "stipend", "hsa", "premium")):
                if profile.benefits:
                    lines.extend(
                        f"{b.name}: {b.summary}" for b in profile.benefits
                    )
                    from_records = True
                else:
                    # The plans this organization offers are written down in the
                    # governed benefits guide, which the citations below carry.
                    # What is *not* known is which of them this person is in —
                    # and four invented enrollments used to be printed here as
                    # though it were.
                    lines.append(
                        "No benefits enrollment is on record for you, so I "
                        "cannot say which plans you are in. The governed "
                        "benefits guide describes what is offered."
                    )
            if any(w in lowered for w in ("manager", "who do i ask", "my team",
                                          "report to", "reports to")):
                lines.append(
                    f"You are {profile.title} on {profile.team}"
                    + (f", reporting to {profile.manager}" if profile.manager else "")
                    + "."
                )
                from_records = True

        if hits:
            top = hits[0]
            governing = top.asset
            level = governing.authority_level or "unlevelled"
            if top.grounding >= policy.governing_coverage:
                lines.append(
                    f"The governing document is “{governing.title}” ({level}) "
                    f"— {policy.describe()}, so it is the one that decides. "
                    "The citations below have the full detail."
                )
            else:
                # The middle band. Handing this over is useful; calling it the
                # rule is not, and the gap between the two is the finding:
                # the document that should have answered does not exist.
                lines.append(
                    f"The closest thing in the corpus is “{governing.title}” "
                    f"({level}), and it may not answer this — it covers only "
                    f"part of what you asked. Nothing here governs the "
                    "question, which is worth telling the People team: it "
                    "means the document that should answer it is missing."
                )
            # A conflict a reader is not told about is the failure the whole
            # precedence list exists to prevent — being handed only the winning
            # rule leaves no way to discover that your team follows the other.
            contenders = [
                h for h in hits[1:]
                if h.asset.authority_level != governing.authority_level
            ]
            if contenders:
                lines.append(
                    "Also retrieved, at lower authority: "
                    + "; ".join(
                        f"“{h.asset.title}” ({h.asset.authority_level or 'unlevelled'})"
                        for h in contenders
                    )
                    + ". Where they disagree with the document above, the one "
                    "above governs."
                )
            unpublished = [h for h in hits if h.asset.status != "published"]
            if unpublished:
                lines.append(
                    "Note: "
                    + ", ".join(f"“{h.asset.title}” is {h.asset.status}"
                                for h in unpublished)
                    + " — retrievable, but not authoritative."
                )
        elif from_records:
            lines.append(
                "That is from your own record; no governed document covered "
                "enough of the question to cite alongside it."
            )
        else:
            lines.append(
                "I could not find that in the HR knowledge or your records. "
                "Nothing in the corpus covered enough of the question to be "
                "worth citing, so I would rather say so than hand you the "
                "closest document. The People team owns this — ask in "
                "#people-ops."
            )
        return "\n\n".join(lines)


def build_employee_service(
    backend: str,
    project: str | None,
    collection: str,
    knowledge,
    tasks: TaskService,
    graph: GraphService,
    teams=None,
    dispatcher=None,
) -> EmployeeService:
    directory: EmployeeDirectory
    if backend == "firestore":
        directory = FirestoreEmployeeDirectory(project, collection)
    else:
        directory = InMemoryEmployeeDirectory()
    return EmployeeService(
        directory, knowledge, tasks, graph, teams, dispatcher
    )
