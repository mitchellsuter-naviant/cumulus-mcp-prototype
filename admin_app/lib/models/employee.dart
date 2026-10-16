/// Employee Hub models. Mirrors `server/app/employee.py`.
library;

class Benefit {
  final String name;
  final String kind;
  final String summary;
  final String status;

  const Benefit({
    required this.name,
    required this.kind,
    required this.summary,
    required this.status,
  });

  factory Benefit.fromJson(Map<String, dynamic> json) => Benefit(
    name: json['name'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    status: json['status'] as String? ?? 'enrolled',
  );
}

/// The hub's view of a person.
///
/// Keyed by **email**, because that is the `Employee` object's natural key in
/// the graph — the same key the calendar and the reimbursement rows use. The
/// org-chart half (name, title, team, manager, location) is read through from
/// the graph on every call rather than stored here, so it cannot rot; only the
/// balance, the accrual and the benefits are the hub's own state.
///
/// That second half is **nullable and usually null**: an HRIS is its source
/// and none delivers it yet. The server used to mint a balance, an accrual, a
/// company holiday and four benefit plans the first time anybody opened the
/// page, and this screen printed all of it as the reader's own record.
class EmployeeProfile {
  final String email;
  final String name;
  final String title;
  final String team;
  final String manager;
  final String location;

  /// False for a principal the org chart has never heard of — Naviant Support
  /// is the standing case. [workforceNote] says so in a sentence, so an empty
  /// hub reads as correct rather than broken.
  final bool inWorkforce;
  final String workforceNote;

  /// **Null when no balance is on record** — which is not zero days, and must
  /// never be rendered as zero days. Same for the accrual beside it; the
  /// holiday and the enrolments are empty for the same reason.
  final double? ptoBalanceDays;
  final double? ptoAccrualPerMonth;
  final String nextCompanyHoliday;
  final List<Benefit> benefits;

  /// The server's one sentence explaining why the personal half is empty for
  /// somebody the org chart *does* know. [workforceNote] answers the other
  /// case; this screen shows whichever applies rather than an empty card.
  final String recordsNote;

  /// The governed auto-approval window, parsed from the PTO policy, and the
  /// revision-stamped ref it came from. Served rather than written here: the
  /// hub used to print a literal "3 working days" beside the claim that the
  /// number was governed, so editing the policy moved the rule and not the
  /// sentence.
  final double ptoAutoApproveDays;
  final String ptoPolicyRef;

  const EmployeeProfile({
    required this.email,
    required this.name,
    required this.title,
    required this.team,
    required this.manager,
    required this.location,
    required this.inWorkforce,
    required this.workforceNote,
    this.ptoBalanceDays,
    this.ptoAccrualPerMonth,
    required this.nextCompanyHoliday,
    required this.benefits,
    this.recordsNote = '',
    this.ptoAutoApproveDays = 0,
    this.ptoPolicyRef = '',
  });

  factory EmployeeProfile.fromJson(Map<String, dynamic> json) =>
      EmployeeProfile(
        email: json['email'] as String? ?? '',
        name: json['name'] as String? ?? '',
        title: json['title'] as String? ?? '',
        team: json['team'] as String? ?? '',
        manager: json['manager'] as String? ?? '',
        location: json['location'] as String? ?? '',
        inWorkforce: json['in_workforce'] as bool? ?? false,
        workforceNote: json['workforce_note'] as String? ?? '',
        ptoBalanceDays: (json['pto_balance_days'] as num?)?.toDouble(),
        ptoAccrualPerMonth: (json['pto_accrual_per_month'] as num?)?.toDouble(),
        nextCompanyHoliday: json['next_company_holiday'] as String? ?? '',
        recordsNote: json['records_note'] as String? ?? '',
        benefits: (json['benefits'] as List<dynamic>? ?? const [])
            .map((e) => Benefit.fromJson(e as Map<String, dynamic>))
            .toList(),
        ptoAutoApproveDays:
            (json['pto_auto_approve_days'] as num? ?? 0).toDouble(),
        ptoPolicyRef: json['pto_policy_ref'] as String? ?? '',
      );
}

class PtoRequest {
  final String id;
  final String startDate;
  final String endDate;
  final double days;
  final String note;
  final String state; // approved | pending | denied
  final String reason;
  final String taskId;
  final String decidedBy;

  const PtoRequest({
    required this.id,
    required this.startDate,
    required this.endDate,
    required this.days,
    required this.note,
    required this.state,
    required this.reason,
    required this.taskId,
    required this.decidedBy,
  });

  factory PtoRequest.fromJson(Map<String, dynamic> json) => PtoRequest(
    id: json['id'] as String,
    startDate: json['start_date'] as String? ?? '',
    endDate: json['end_date'] as String? ?? '',
    days: (json['days'] as num? ?? 0).toDouble(),
    note: json['note'] as String? ?? '',
    state: json['state'] as String? ?? 'pending',
    reason: json['reason'] as String? ?? '',
    taskId: json['task_id'] as String? ?? '',
    decidedBy: json['decided_by'] as String? ?? '',
  );
}

class HrCitation {
  final String id;
  final String title;
  final String path;

  /// null for machinery — a prompt, a config, a generated schema export. The
  /// agent no longer cites those at an employee, but the field stays nullable
  /// because the corpus is what decides, not this screen.
  final String? authorityLevel;
  final String status;

  /// How well this document is grounded in the question, as the server
  /// measured it — the better of the lexical and semantic signals.
  ///
  /// **Null when nothing measured it.** A planner-backed answer cites the
  /// documents it read during its own search, and those carry no ranking
  /// number; defaulting them to zero put "covers 0% of the question" under
  /// every citation on the deployed console, which is a measurement claim
  /// nobody made.
  final double? grounding;

  const HrCitation({
    required this.id,
    required this.title,
    this.path = '',
    this.authorityLevel,
    this.status = 'published',
    this.grounding,
  });

  factory HrCitation.fromJson(Map<String, dynamic> json) => HrCitation(
    id: json['id'] as String? ?? '',
    title: json['title'] as String? ?? '',
    path: json['path'] as String? ?? '',
    authorityLevel: json['authority_level'] as String?,
    status: json['status'] as String? ?? 'published',
    grounding: (json['grounding'] as num?)?.toDouble(),
  );
}

/// What an answer was allowed to stand on, and under which rule.
///
/// Returned rather than kept, because "the governing document is X" is a claim
/// about precedence and a reader has no way to check it otherwise.
class HrGrounding {
  /// `knowledge:retrieval-authority@revN` — the precedence this ranked under.
  final String policyRef;

  /// `policy > standard > guideline > how-to > note`.
  final String precedence;

  /// The coverage a document had to clear to be citable at all, and how many
  /// retrieved documents fell below it and were dropped.
  final double minCoverage;
  final int belowFloor;

  /// The query retrieval actually ran, which is not always the message — a
  /// follow-up carries the turn before it.
  final String query;

  const HrGrounding({
    this.policyRef = '',
    this.precedence = '',
    this.minCoverage = 0,
    this.belowFloor = 0,
    this.query = '',
  });

  factory HrGrounding.fromJson(Map<String, dynamic> json) => HrGrounding(
    policyRef: json['policy_ref'] as String? ?? '',
    precedence: json['precedence'] as String? ?? '',
    minCoverage: (json['min_coverage'] as num?)?.toDouble() ?? 0,
    belowFloor: (json['below_floor'] as num?)?.toInt() ?? 0,
    query: json['query'] as String? ?? '',
  );
}

class HrChatReply {
  final String reply;
  final List<HrCitation> citations;
  final String backend;

  /// The employee's own facts the answer was allowed to use. Empty for a
  /// principal the org chart has never heard of — which is a state the hub
  /// shows rather than papering over with zeros.
  final List<String> groundedOn;
  final HrGrounding grounding;

  const HrChatReply({
    required this.reply,
    required this.citations,
    required this.backend,
    this.groundedOn = const [],
    this.grounding = const HrGrounding(),
  });

  factory HrChatReply.fromJson(Map<String, dynamic> json) => HrChatReply(
    reply: json['reply'] as String? ?? '',
    citations: (json['citations'] as List<dynamic>? ?? const [])
        .map((e) => HrCitation.fromJson(e as Map<String, dynamic>))
        .toList(),
    backend: json['backend'] as String? ?? '',
    groundedOn: (json['grounded_on'] as List<dynamic>? ?? const [])
        .map((e) => e as String)
        .toList(),
    grounding: HrGrounding.fromJson(
      (json['grounding'] as Map<String, dynamic>?) ?? const {},
    ),
  );
}

/// One turn of the conversation, as the console holds and replays it.
///
/// The client owns the transcript: a surface that answers from somebody's own
/// health plan and PTO is the last place to grow a server-side store of who
/// asked what, and "new chat" is only honest if the history it drops was never
/// the server's to keep.
class HrTurn {
  final bool fromAgent;
  final String text;

  const HrTurn({required this.fromAgent, required this.text});

  Map<String, dynamic> toJson() => {
    'role': fromAgent ? 'agent' : 'employee',
    'text': text,
  };
}
