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

  FlowVersion({
    required this.version,
    required this.label,
    required this.status,
    required this.steps,
    required this.modelBudgetUsdPerRun,
    required this.notes,
    required this.authoredBy,
    required this.createdAt,
  });

  bool get isDraft => status == 'draft';
  bool get isActive => status == 'active';
  String get display => label.isEmpty ? 'v$version' : label;

  factory FlowVersion.fromJson(Map<String, dynamic> j) => FlowVersion(
    version: _i(j['version']),
    label: j['label'] as String? ?? '',
    status: j['status'] as String? ?? 'archived',
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
  final String status; // draft | active | paused | deprecated
  final String description;
  final String team;
  final List<String> tags;
  final int version;
  final DateTime updatedAt;
  final int runs30d;
  final double cost30dUsd;
  final double? success30d;
  final int tasksOpen;
  final double minutesSaved30d;
  final List<int> spark;

  FlowSummary({
    required this.id,
    required this.name,
    required this.status,
    required this.description,
    required this.team,
    required this.tags,
    required this.version,
    required this.updatedAt,
    required this.runs30d,
    required this.cost30dUsd,
    required this.success30d,
    required this.tasksOpen,
    required this.minutesSaved30d,
    required this.spark,
  });

  factory FlowSummary.fromJson(Map<String, dynamic> j) => FlowSummary(
    id: j['id'] as String,
    name: j['name'] as String? ?? j['id'] as String,
    status: j['status'] as String,
    description: j['description'] as String? ?? '',
    team: j['team'] as String? ?? '',
    tags: _strings(j['tags']),
    version: _i(j['version']),
    updatedAt: _date(j['updated_at']),
    runs30d: _i(j['runs_30d']),
    cost30dUsd: _d(j['cost_30d_usd']),
    success30d: j['success_30d'] == null ? null : _d(j['success_30d']),
    tasksOpen: _i(j['tasks_open']),
    minutesSaved30d: _d(j['minutes_saved_30d']),
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
         description: base.description,
         team: base.team,
         tags: base.tags,
         version: base.version,
         updatedAt: base.updatedAt,
         runs30d: base.runs30d,
         cost30dUsd: base.cost30dUsd,
         success30d: base.success30d,
         tasksOpen: base.tasksOpen,
         minutesSaved30d: base.minutesSaved30d,
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

  FleetSeriesDay({
    required this.date,
    required this.runs,
    required this.costUsd,
    required this.tasksRaised,
  });

  factory FleetSeriesDay.fromJson(Map<String, dynamic> j) => FleetSeriesDay(
    date: j['date'] as String,
    runs: _i(j['runs']),
    costUsd: _d(j['cost_usd']),
    tasksRaised: _i(j['tasks_raised']),
  );
}

class FleetAttention {
  final String flowId;
  final String reason;

  FleetAttention({required this.flowId, required this.reason});

  factory FleetAttention.fromJson(Map<String, dynamic> j) => FleetAttention(
    flowId: j['flow_id'] as String,
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

