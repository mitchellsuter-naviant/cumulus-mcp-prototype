/// Models for the Flows section — the registry and observability plane
/// for authored logic. One primitive: a Flow.
///
/// Mirrors `server/app/flows_api.py`. Costs come from audited ModelCall
/// records. Runs are not here: they are durable records, modelled in
/// `run.dart` against `server/app/runs.py`.
library;

double _d(dynamic v) => v == null ? 0 : (v as num).toDouble();
int _i(dynamic v) => v == null ? 0 : (v as num).toInt();

List<String> _strings(dynamic v) =>
    (v as List<dynamic>? ?? const []).map((e) => e.toString()).toList();

DateTime _date(dynamic v) =>
    DateTime.tryParse(v?.toString() ?? '')?.toLocal() ?? DateTime.now();

class FlowStep {
  final String id;
  final String type; // trigger | modelcall | transform | rule | task |
  // action | event | sync | check | resolve
  final String name;
  final String summary;
  final Map<String, String> config;
  final List<String> next;
  final List<String> branchLabels;

  FlowStep({
    required this.id,
    required this.type,
    required this.name,
    required this.summary,
    required this.config,
    required this.next,
    required this.branchLabels,
  });

  factory FlowStep.fromJson(Map<String, dynamic> j) => FlowStep(
    id: j['id'] as String,
    type: j['type'] as String,
    name: j['name'] as String,
    summary: j['summary'] as String? ?? '',
    config: (j['config'] as Map<String, dynamic>? ?? const {}).map(
      (k, v) => MapEntry(k, v.toString()),
    ),
    next: _strings(j['next']),
    branchLabels: _strings(j['branch_labels']),
  );
}

/// One published or drafted revision of a definition — a full snapshot of the
/// declarative export, never a delta.
class FlowVersion {
  final int version;
  final String label;
  final String status; // active | archived | draft
  final List<FlowStep> steps;
  final double modelBudgetUsdPerRun;
  final String notes;
  final String authoredBy;
  final DateTime createdAt;

  /// Whether this version's executable package is in the deployed server code.
  ///
  /// A definition and the code that runs it are two artifacts, and a console
  /// that hides the gap will happily offer to make live a version nothing can
  /// execute. So the server says which versions it actually ships, and only
  /// those are offered for activation. False when the server does not say —
  /// the safe reading of silence about a deployment.
  final bool shipped;

  FlowVersion({
    required this.version,
    required this.label,
    required this.status,
    required this.steps,
    required this.modelBudgetUsdPerRun,
    required this.notes,
    required this.authoredBy,
    required this.createdAt,
    this.shipped = false,
  });

  bool get isDraft => status == 'draft';
  bool get isActive => status == 'active';
  String get display => label.isEmpty ? 'v$version' : label;

  /// Ready to be made live: the code exists and this is not already the one
  /// running.
  bool get canActivate => shipped && !isActive;

  factory FlowVersion.fromJson(Map<String, dynamic> j) => FlowVersion(
    version: _i(j['version']),
    label: j['label'] as String? ?? '',
    status: j['status'] as String? ?? 'archived',
    shipped: j['shipped'] as bool? ?? false,
    steps: (j['steps'] as List<dynamic>? ?? const [])
        .map((s) => FlowStep.fromJson(s as Map<String, dynamic>))
        .toList(),
    modelBudgetUsdPerRun: _d(j['model_budget_usd_per_run']),
    notes: j['notes'] as String? ?? '',
    authoredBy: j['authored_by'] as String? ?? '',
    createdAt: _date(j['created_at']),
  );
}

/// A step annotated against the version being compared with.
class StepDiff {
  final FlowStep step;
  final String change; // unchanged | added | removed | modified
  final List<String> changedFields;

  StepDiff({
    required this.step,
    required this.change,
    required this.changedFields,
  });

  factory StepDiff.fromJson(Map<String, dynamic> j) => StepDiff(
    step: FlowStep.fromJson(j['step'] as Map<String, dynamic>),
    change: j['change'] as String? ?? 'unchanged',
    changedFields: _strings(j['changed_fields']),
  );
}

/// One cost or performance insight, with the evidence it was derived from and
/// the exact change it proposes (null when the card is advisory).
class Recommendation {
  final String id;
  final String kind;
  final String title;
  final String detail;
  final String evidence;
  final String stepId;
  final String stepName;
  final double estimatedMonthlySavingsUsd;
  final double confidence;
  final Map<String, String>? proposedChange;

  Recommendation({
    required this.id,
    required this.kind,
    required this.title,
    required this.detail,
    required this.evidence,
    required this.stepId,
    required this.stepName,
    required this.estimatedMonthlySavingsUsd,
    required this.confidence,
    required this.proposedChange,
  });

  bool get applicable => proposedChange != null;

  factory Recommendation.fromJson(Map<String, dynamic> j) => Recommendation(
    id: j['id'] as String,
    kind: j['kind'] as String,
    title: j['title'] as String? ?? '',
    detail: j['detail'] as String? ?? '',
    evidence: j['evidence'] as String? ?? '',
    stepId: j['step_id'] as String? ?? '',
    stepName: j['step_name'] as String? ?? '',
    estimatedMonthlySavingsUsd: _d(j['estimated_monthly_savings_usd']),
    confidence: _d(j['confidence']),
    proposedChange: (j['proposed_change'] as Map<String, dynamic>?)?.map(
      (k, v) => MapEntry(k, v.toString()),
    ),
  );
}

/// What applying a recommendation produced: the draft it staged. Measuring it
/// is a separate, deliberate act — the flow's eval spec, run on purpose —
/// rather than something a card launches on the operator's behalf.
class AppliedRecommendation {
  final FlowVersion draft;
  final String summary;

  AppliedRecommendation({required this.draft, required this.summary});

  factory AppliedRecommendation.fromJson(Map<String, dynamic> j) =>
      AppliedRecommendation(
        draft: FlowVersion.fromJson(j['draft'] as Map<String, dynamic>),
        summary: j['summary'] as String? ?? '',
      );
}

class FlowSummary {
  final String id;
  final String name;
  final String status; // draft | active | paused | deprecated | undeclared

  /// Whether anyone authored a Tier-2 export for this flow. A sync feed runs
  /// and writes the graph with no definition behind it, and the fleet row has
  /// to be able to say so rather than showing empty columns without a reason.
  final bool hasDefinition;
  final String description;
  final String team;
  final List<String> tags;
  final int version;
  final DateTime updatedAt;
  /// How many days every figure below covers. The caller chooses it, so the
  /// row states it: a field named for a fixed window while holding another
  /// one is the quiet kind of lie.
  final int windowDays;
  final int runsWindow;
  final double costWindowUsd;
  final double? successWindow;

  /// Failed runs in the window, beside the rate rather than instead of it:
  /// three failures out of four and three hundred out of four hundred are the
  /// same percentage and not the same finding.
  final int errorsWindow;

  /// `healthy` · `degraded` · `failed` · `draft` · `idle`. `idle` is a real
  /// verdict: a flow that has finished nothing has no success rate, and
  /// calling that healthy is a claim over an empty sample.
  final String health;

  /// `measured` · `partial` · `unrecorded` · `no-model-calls` — whether this
  /// row's spend is a measurement or a hole.
  final String costBasisWindow;

  /// What kind of fact starts this flow (`event` · `schedule` · `webhook` ·
  /// `manual`) and which one. Both blank on a flow with no declarative
  /// export — there is no trigger step to read.
  final String triggerKind;
  final String triggerDetail;

  /// The last run at any depth of history, and how it ended. Empty when the
  /// flow has never run.
  final String lastRunAt;
  final String lastRunStatus;
  final int tasksOpen;
  final double minutesSavedWindow;
  final List<int> spark;

  FlowSummary({
    required this.id,
    required this.name,
    required this.status,
    required this.hasDefinition,
    required this.description,
    required this.team,
    required this.tags,
    required this.version,
    required this.updatedAt,
    required this.windowDays,
    required this.runsWindow,
    required this.costWindowUsd,
    required this.successWindow,
    required this.errorsWindow,
    required this.health,
    required this.costBasisWindow,
    required this.triggerKind,
    required this.triggerDetail,
    required this.lastRunAt,
    required this.lastRunStatus,
    required this.tasksOpen,
    required this.minutesSavedWindow,
    required this.spark,
  });

  factory FlowSummary.fromJson(Map<String, dynamic> j) => FlowSummary(
    id: j['id'] as String,
    name: j['name'] as String? ?? j['id'] as String,
    status: j['status'] as String,
    hasDefinition: j['has_definition'] as bool? ?? true,
    description: j['description'] as String? ?? '',
    team: j['team'] as String? ?? '',
    tags: _strings(j['tags']),
    version: _i(j['version']),
    updatedAt: _date(j['updated_at']),
    windowDays: j['window_days'] == null ? 30 : _i(j['window_days']),
    runsWindow: _i(j['runs_window']),
    costWindowUsd: _d(j['cost_window_usd']),
    successWindow: j['success_window'] == null ? null : _d(j['success_window']),
    errorsWindow: _i(j['errors_window']),
    health: j['health'] as String? ?? 'idle',
    costBasisWindow: j['cost_basis_window'] as String? ?? 'no-model-calls',
    triggerKind: j['trigger_kind'] as String? ?? '',
    triggerDetail: j['trigger_detail'] as String? ?? '',
    lastRunAt: j['last_run_at'] as String? ?? '',
    lastRunStatus: j['last_run_status'] as String? ?? '',
    tasksOpen: _i(j['tasks_open']),
    minutesSavedWindow: _d(j['minutes_saved_window']),
    spark: (j['spark'] as List<dynamic>? ?? const []).map(_i).toList(),
  );
}

class FlowDetail extends FlowSummary {
  final List<FlowStep> steps;
  final List<String> queues;
  final String workspace;
  final double roiMinutesPerRun;
  final double roiHourlyRateUsd;
  final double modelBudgetUsdPerRun;
  final String authoredBy;
  final DateTime createdAt;
  final String activeVersionLabel;
  final String draftVersionLabel;
  final int versionCount;

  FlowDetail({
    required FlowSummary base,
    required this.steps,
    required this.queues,
    required this.workspace,
    required this.roiMinutesPerRun,
    required this.roiHourlyRateUsd,
    required this.modelBudgetUsdPerRun,
    required this.authoredBy,
    required this.createdAt,
    required this.activeVersionLabel,
    required this.draftVersionLabel,
    required this.versionCount,
  }) : super(
         id: base.id,
         name: base.name,
         status: base.status,
         hasDefinition: base.hasDefinition,
         description: base.description,
         team: base.team,
         tags: base.tags,
         version: base.version,
         updatedAt: base.updatedAt,
         windowDays: base.windowDays,
         runsWindow: base.runsWindow,
         costWindowUsd: base.costWindowUsd,
         successWindow: base.successWindow,
         errorsWindow: base.errorsWindow,
         health: base.health,
         costBasisWindow: base.costBasisWindow,
         triggerKind: base.triggerKind,
         triggerDetail: base.triggerDetail,
         lastRunAt: base.lastRunAt,
         lastRunStatus: base.lastRunStatus,
         tasksOpen: base.tasksOpen,
         minutesSavedWindow: base.minutesSavedWindow,
         spark: base.spark,
       );

  factory FlowDetail.fromJson(Map<String, dynamic> j) => FlowDetail(
    base: FlowSummary.fromJson(j),
    steps: (j['steps'] as List<dynamic>? ?? const [])
        .map((s) => FlowStep.fromJson(s as Map<String, dynamic>))
        .toList(),
    queues: _strings(j['queues']),
    workspace: j['workspace'] as String? ?? '',
    roiMinutesPerRun: _d(j['roi_minutes_per_run']),
    roiHourlyRateUsd: _d(j['roi_hourly_rate_usd']),
    modelBudgetUsdPerRun: _d(j['model_budget_usd_per_run']),
    authoredBy: j['authored_by'] as String? ?? '',
    createdAt: _date(j['created_at']),
    activeVersionLabel: j['active_version_label'] as String? ?? '',
    draftVersionLabel: j['draft_version_label'] as String? ?? '',
    versionCount: _i(j['version_count']),
  );
}

class FleetSeriesDay {
  final String date;
  final int runs;
  final double costUsd;
  final int tasksRaised;
  final int completed;
  final int failed;

  FleetSeriesDay({
    required this.date,
    required this.runs,
    required this.costUsd,
    required this.tasksRaised,
    required this.completed,
    required this.failed,
  });

  /// Over finished runs only. A day whose runs are all still in flight has no
  /// success rate — not a zero — and the chart has to skip it rather than
  /// paint a floor that nothing measured.
  double? get successRate {
    final finished = completed + failed;
    return finished == 0 ? null : completed / finished;
  }

  factory FleetSeriesDay.fromJson(Map<String, dynamic> j) => FleetSeriesDay(
    date: j['date'] as String,
    runs: _i(j['runs']),
    costUsd: _d(j['cost_usd']),
    tasksRaised: _i(j['tasks_raised']),
    completed: _i(j['completed']),
    failed: _i(j['failed']),
  );
}

class FleetAttention {
  final String flowId;

  /// `failure-rate` · `no-eval-spec` · `no-export`. The console switches on
  /// this to pick a row tag; [reason] stays the sentence a person reads.
  final String kind;
  final String reason;

  FleetAttention({
    required this.flowId,
    required this.kind,
    required this.reason,
  });

  factory FleetAttention.fromJson(Map<String, dynamic> j) => FleetAttention(
    flowId: j['flow_id'] as String,
    kind: j['kind'] as String? ?? '',
    reason: j['reason'] as String? ?? '',
  );
}

class FleetStats {
  final int days;
  final int definitions;

  /// Every flow that actually runs, which is not the same as every flow that
  /// has a declarative export: the five sync feeds write the graph and have no
  /// `FlowDefinition`. The fleet is the former.
  final int flows;
  final int withoutDefinition;

  /// `measured` · `partial` · `unrecorded` · `no-model-calls`. A spend of zero
  /// means two different things and the tile must not conflate them.
  final String costBasis;
  final int pricedModelCalls;

  /// One sentence naming the assumption the ROI figure rests on.
  final String roiStatement;
  final int active;
  final int paused;
  final int draft;
  final int runs;
  final int completed;

  /// Runs that ended in failure over the window. The console leads with this
  /// rather than with a success rate: a rate reads as fine at 97% over a
  /// thousand runs and as catastrophic at 97% over thirty, and the number a
  /// reader can act on is how many actually broke.
  final int failed;
  final double? successRate;
  final double modelSpendUsd;
  final int tokens;
  final int modelCalls;
  final double hoursReturned;
  final double roiUsd;
  final int tasksOpen;
  final int escalated;
  final List<FleetSeriesDay> series;
  final List<FleetAttention> attention;

  FleetStats({
    required this.days,
    required this.definitions,
    required this.flows,
    required this.withoutDefinition,
    required this.costBasis,
    required this.pricedModelCalls,
    required this.roiStatement,
    required this.active,
    required this.paused,
    required this.draft,
    required this.runs,
    required this.completed,
    required this.failed,
    required this.successRate,
    required this.modelSpendUsd,
    required this.tokens,
    required this.modelCalls,
    required this.hoursReturned,
    required this.roiUsd,
    required this.tasksOpen,
    required this.escalated,
    required this.series,
    required this.attention,
  });

  factory FleetStats.fromJson(Map<String, dynamic> j) => FleetStats(
    days: _i(j['days']),
    definitions: _i(j['definitions']),
    flows: _i(j['flows']),
    withoutDefinition: _i(j['without_definition']),
    costBasis: j['cost_basis'] as String? ?? '',
    pricedModelCalls: _i(j['priced_model_calls']),
    roiStatement:
        ((j['roi_basis'] as Map<String, dynamic>?)?['statement'] as String?) ??
        '',
    active: _i(j['active']),
    paused: _i(j['paused']),
    draft: _i(j['draft']),
    runs: _i(j['runs']),
    completed: _i(j['completed']),
    failed: _i(j['failed']),
    successRate: j['success_rate'] == null ? null : _d(j['success_rate']),
    modelSpendUsd: _d(j['model_spend_usd']),
    tokens: _i(j['tokens']),
    modelCalls: _i(j['model_calls']),
    hoursReturned: _d(j['hours_returned']),
    roiUsd: _d(j['roi_usd']),
    tasksOpen: _i(j['tasks_open']),
    escalated: _i(j['escalated']),
    series: (j['series'] as List<dynamic>? ?? const [])
        .map((s) => FleetSeriesDay.fromJson(s as Map<String, dynamic>))
        .toList(),
    attention: (j['attention'] as List<dynamic>? ?? const [])
        .map((s) => FleetAttention.fromJson(s as Map<String, dynamic>))
        .toList(),
  );
}

class UsageDay {
  final String date;
  final int runs;
  final int completed;
  final int tasksRaised;
  final int escalated;
  final int failed;
  final double costUsd;
  final int tokens;
  final double minutesSaved;

  UsageDay({
    required this.date,
    required this.runs,
    required this.completed,
    required this.tasksRaised,
    required this.escalated,
    required this.failed,
    required this.costUsd,
    required this.tokens,
    required this.minutesSaved,
  });

  factory UsageDay.fromJson(Map<String, dynamic> j) => UsageDay(
    date: j['date'] as String,
    runs: _i(j['runs']),
    completed: _i(j['completed']),
    tasksRaised: _i(j['tasks_raised']),
    escalated: _i(j['escalated']),
    failed: _i(j['failed']),
    costUsd: _d(j['cost_usd']),
    tokens: _i(j['tokens']),
    minutesSaved: _d(j['minutes_saved']),
  );
}

class FlowUsage {
  final int days;
  final int runs;
  final int completed;

  /// Runs that ended in failure over the window.
  final int failed;
  final double? successRate;
  final double costUsd;
  final int tokens;
  final int modelCalls;
  final int tasksRaised;
  final int escalated;
  final double minutesSaved;
  final double roiUsd;
  final List<UsageDay> series;

  FlowUsage({
    required this.days,
    required this.runs,
    required this.completed,
    required this.failed,
    required this.successRate,
    required this.costUsd,
    required this.tokens,
    required this.modelCalls,
    required this.tasksRaised,
    required this.escalated,
    required this.minutesSaved,
    required this.roiUsd,
    required this.series,
  });

  factory FlowUsage.fromJson(Map<String, dynamic> j) => FlowUsage(
    days: _i(j['days']),
    runs: _i(j['runs']),
    completed: _i(j['completed']),
    failed: _i(j['failed']),
    successRate: j['success_rate'] == null ? null : _d(j['success_rate']),
    costUsd: _d(j['cost_usd']),
    tokens: _i(j['tokens']),
    modelCalls: _i(j['model_calls']),
    tasksRaised: _i(j['tasks_raised']),
    escalated: _i(j['escalated']),
    minutesSaved: _d(j['minutes_saved']),
    roiUsd: _d(j['roi_usd']),
    series: (j['series'] as List<dynamic>? ?? const [])
        .map((s) => UsageDay.fromJson(s as Map<String, dynamic>))
        .toList(),
  );
}


// ---------------------------------------------------------------------------
// Inventory — what a flow is wired to
// ---------------------------------------------------------------------------

/// One model this flow calls, declared and observed.
///
/// The two halves never merge. A model named in the export with no calls
/// behind it is a declaration nothing has exercised; calls with no
/// declaration are a model the map does not admit to. Both are worth seeing,
/// and a single reconciled number would hide either one.
class ModelInventory {
  final String model;

  /// The steps whose exported config names this model.
  final List<String> steps;
  final bool declared;
  final int calls;
  final int tokens;
  final double costUsd;

  /// How many of [calls] carried a price. A $0.00 spend over priced calls is
  /// free; over unpriced calls it is a hole, and the panel says which.
  final int pricedCalls;

  ModelInventory({
    required this.model,
    required this.steps,
    required this.declared,
    required this.calls,
    required this.tokens,
    required this.costUsd,
    required this.pricedCalls,
  });

  /// Whether this model is only ever inferred — the export named none, and
  /// the deployed server's default answered for it.
  bool get isDefault => model == 'server default';

  factory ModelInventory.fromJson(Map<String, dynamic> j) => ModelInventory(
    model: j['model'] as String? ?? '',
    steps: _strings(j['steps']),
    declared: j['declared'] == true,
    calls: _i(j['calls']),
    tokens: _i(j['tokens']),
    costUsd: _d(j['cost_usd']),
    pricedCalls: _i(j['priced_calls']),
  );
}

/// One way this flow touches something governed.
///
/// A single shape for every relationship, because the console shows them as
/// one table: a reader comparing "reads Invoice" against "mutates SalesOrder"
/// should not have to reconcile two row layouts to do it. The verb carries
/// the distinction.
///
/// [ref] is the addressable form — `graph:Invoice`, `knowledge:ap-policy`,
/// `collection:crm-companies`, `queue:order-desk`, `system:erp` — so a click
/// routes on the address rather than on the label it happens to render.
class FlowLink {
  /// reads · mutates · establishes · writes-to · routes-to · reaches
  final String verb;
  final String ref;

  /// entity · knowledge · collection · link · queue · system
  final String kind;
  final String label;
  final bool declared;
  final bool observed;

  FlowLink({
    required this.verb,
    required this.ref,
    required this.kind,
    required this.label,
    required this.declared,
    required this.observed,
  });

  /// Declared and never seen — a path no run has taken yet, or a stale claim.
  /// Drawn back rather than flagged, because the table cannot tell which.
  bool get unobserved => declared && !observed;

  /// How this row reads in the evidence column.
  String get evidence => observed
      ? (declared ? 'observed · declared' : 'observed')
      : 'declared';

  factory FlowLink.fromJson(Map<String, dynamic> j) => FlowLink(
    verb: j['verb'] as String? ?? '',
    ref: j['ref'] as String? ?? '',
    kind: j['kind'] as String? ?? '',
    label: j['label'] as String? ?? '',
    declared: j['declared'] == true,
    observed: j['observed'] == true,
  );
}

/// What one flow is wired to. Not windowed: a wiring the last thirty days did
/// not happen to exercise is still the wiring.
class FlowInventory {
  final String flowId;

  /// `never_ran` · `ran_but_wrote_nothing` · `observed`. A flow that never ran
  /// and one that ran and wrote nothing both have empty writes, and they are
  /// opposite facts.
  final String state;
  final int scannedRuns;
  final int totalRuns;
  final List<ModelInventory> models;
  final double modelBudgetUsdPerRun;
  /// Every governed thing it touches, grouped by verb in the order the
  /// console lists them. Sorted server-side, so the table needs no grouping
  /// pass of its own.
  final List<FlowLink> links;
  final List<String> undeclared;
  final List<String> unobserved;

  FlowInventory({
    required this.flowId,
    required this.state,
    required this.scannedRuns,
    required this.totalRuns,
    required this.models,
    required this.modelBudgetUsdPerRun,
    required this.links,
    required this.undeclared,
    required this.unobserved,
  });

  bool get isEmpty => models.isEmpty && links.isEmpty;

  /// The links carrying one verb, in the order the server sent them.
  List<FlowLink> withVerb(String verb) =>
      links.where((l) => l.verb == verb).toList();

  factory FlowInventory.fromJson(Map<String, dynamic> j) => FlowInventory(
    flowId: j['flow_id'] as String? ?? '',
    state: j['state'] as String? ?? '',
    scannedRuns: _i(j['scanned_runs']),
    totalRuns: _i(j['total_runs']),
    models: (j['models'] as List<dynamic>? ?? const [])
        .map((e) => ModelInventory.fromJson(e as Map<String, dynamic>))
        .toList(),
    modelBudgetUsdPerRun: _d(j['model_budget_usd_per_run']),
    links: (j['links'] as List<dynamic>? ?? const [])
        .map((e) => FlowLink.fromJson(e as Map<String, dynamic>))
        .toList(),
    undeclared: _strings(j['undeclared']),
    unobserved: _strings(j['unobserved']),
  );
}
