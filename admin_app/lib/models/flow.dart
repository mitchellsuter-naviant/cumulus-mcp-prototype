/// Models for the Flows section — the registry and observability plane
/// for authored logic. One primitive: a Flow.
///
/// Mirrors `server/app/flows_api.py`. Costs come from audited ModelCall
/// records; the run journal and audit entries are the governance trail.
library;

double _d(dynamic v) => v == null ? 0 : (v as num).toDouble();
int _i(dynamic v) => v == null ? 0 : (v as num).toInt();

List<String> _strings(dynamic v) =>
    (v as List<dynamic>? ?? const []).map((e) => e.toString()).toList();

DateTime _date(dynamic v) =>
    DateTime.tryParse(v?.toString() ?? '')?.toLocal() ?? DateTime.now();

class FlowStep {
  final String id;
  final String type; // trigger | modelcall | rule | task | action | event | transform
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

/// One canonical fact a flow's contract declares — the stable path ground
/// truth attaches to, never a step id.
class ContractFact {
  final String path;
  final String type; // money | string | ref | verdict | bool | int | score
  final String kind; // extraction | verdict | commit | synthesis
  final String description;

  ContractFact({
    required this.path,
    required this.type,
    required this.kind,
    required this.description,
  });

  factory ContractFact.fromJson(Map<String, dynamic> j) => ContractFact(
    path: j['path'] as String,
    type: j['type'] as String? ?? 'string',
    kind: j['kind'] as String? ?? 'extraction',
    description: j['description'] as String? ?? '',
  );
}

/// The declared facts plus the derived {fact path -> step id} projection for
/// one version — recomputed per version, which is what keeps labels stable
/// while the topology morphs.
class FlowContractInfo {
  final String versionLabel;
  final List<ContractFact> facts;
  final Map<String, String> producerMap;

  FlowContractInfo({
    required this.versionLabel,
    required this.facts,
    required this.producerMap,
  });

  factory FlowContractInfo.fromJson(Map<String, dynamic> j) =>
      FlowContractInfo(
        versionLabel: j['version_label'] as String? ?? '',
        facts: (j['facts'] as List<dynamic>? ?? const [])
            .map((f) => ContractFact.fromJson(f as Map<String, dynamic>))
            .toList(),
        producerMap: (j['producer_map'] as Map<String, dynamic>? ?? const {})
            .map((k, v) => MapEntry(k, v.toString())),
      );
}

/// A claim about a canonical fact for one document.
class FactAssertion {
  final String path;
  final String op; // equals | approx | one_of | judge
  final String expected;
  final String tolerance;
  final String rubric;

  FactAssertion({
    required this.path,
    required this.op,
    required this.expected,
    required this.tolerance,
    required this.rubric,
  });

  factory FactAssertion.fromJson(Map<String, dynamic> j) => FactAssertion(
    path: j['path'] as String,
    op: j['op'] as String? ?? 'equals',
    expected: j['expected'] as String? ?? '',
    tolerance: j['tolerance'] as String? ?? '',
    rubric: j['rubric'] as String? ?? '',
  );

  Map<String, dynamic> toJson() => {
    'path': path,
    'op': op,
    'expected': expected,
    if (tolerance.isNotEmpty) 'tolerance': tolerance,
    if (rubric.isNotEmpty) 'rubric': rubric,
  };
}

/// A claim about the path, phrased semantically rather than by step id.
class TrajectoryAssertion {
  final String kind; // action_committed | task_raised | terminal_status | waypoint
  final String expected;

  TrajectoryAssertion({required this.kind, required this.expected});

  factory TrajectoryAssertion.fromJson(Map<String, dynamic> j) =>
      TrajectoryAssertion(
        kind: j['kind'] as String,
        expected: j['expected'] as String? ?? '',
      );

  String get label => switch (kind) {
    'action_committed' => 'actions committed',
    'task_raised' => 'human task raised',
    'terminal_status' => 'terminal status',
    _ => 'waypoint',
  };
}

/// One knowledge/data-layer record the run read, pinned at its revision.
class PinnedRead {
  final String ref;
  final String summary;

  PinnedRead({required this.ref, required this.summary});

  factory PinnedRead.fromJson(Map<String, dynamic> j) => PinnedRead(
    ref: j['ref'] as String,
    summary: j['summary'] as String? ?? '',
  );
}

class GoldenSetRevision {
  final int version;
  final DateTime at;
  final String by;
  final String note;
  final int caseCount;

  GoldenSetRevision({
    required this.version,
    required this.at,
    required this.by,
    required this.note,
    required this.caseCount,
  });

  factory GoldenSetRevision.fromJson(Map<String, dynamic> j) =>
      GoldenSetRevision(
        version: _i(j['version']),
        at: _date(j['at']),
        by: j['by'] as String? ?? '',
        note: j['note'] as String? ?? '',
        caseCount: _i(j['case_count']),
      );
}

/// The golden set: a versioned knowledge asset, plus where its seeded half
/// came from in the repository.
class GoldenSetInfo {
  final List<GoldenCase> cases;
  final int version;
  final List<GoldenSetRevision> revisions;
  final String syncedFrom;
  final DateTime? syncedAt;

  GoldenSetInfo({
    required this.cases,
    required this.version,
    required this.revisions,
    required this.syncedFrom,
    required this.syncedAt,
  });

  factory GoldenSetInfo.fromJson(Map<String, dynamic> j) => GoldenSetInfo(
    cases: (j['cases'] as List<dynamic>? ?? const [])
        .map((c) => GoldenCase.fromJson(c as Map<String, dynamic>))
        .toList(),
    version: _i(j['version']),
    revisions: (j['revisions'] as List<dynamic>? ?? const [])
        .map((r) => GoldenSetRevision.fromJson(r as Map<String, dynamic>))
        .toList(),
    syncedFrom: j['synced_from'] as String? ?? '',
    syncedAt: (j['synced_at'] as String? ?? '').isEmpty
        ? null
        : _date(j['synced_at']),
  );
}

/// One human correction the flywheel appended: raw model output → what the
/// person said it should have been.
class CorrectionPair {
  final String field;
  final String raw;
  final String corrected;
  final String by;
  final DateTime at;

  CorrectionPair({
    required this.field,
    required this.raw,
    required this.corrected,
    required this.by,
    required this.at,
  });

  factory CorrectionPair.fromJson(Map<String, dynamic> j) => CorrectionPair(
    field: j['field'] as String? ?? '',
    raw: j['raw'] as String? ?? '',
    corrected: j['corrected'] as String? ?? '',
    by: j['by'] as String? ?? '',
    at: _date(j['at']),
  );
}

/// The per-ModelCall correction set — the flywheel's auto-accumulated eval
/// dataset for step-level grading.
class CorrectionSet {
  final String stepId;
  final String stepName;
  final String model;
  final int pairs;
  final DateTime? lastAppended;
  final List<CorrectionPair> sample;

  CorrectionSet({
    required this.stepId,
    required this.stepName,
    required this.model,
    required this.pairs,
    required this.lastAppended,
    required this.sample,
  });

  factory CorrectionSet.fromJson(Map<String, dynamic> j) => CorrectionSet(
    stepId: j['step_id'] as String,
    stepName: j['step_name'] as String? ?? '',
    model: j['model'] as String? ?? '',
    pairs: _i(j['pairs']),
    lastAppended: j['last_appended'] == null ? null : _date(j['last_appended']),
    sample: (j['sample'] as List<dynamic>? ?? const [])
        .map((p) => CorrectionPair.fromJson(p as Map<String, dynamic>))
        .toList(),
  );
}

/// One text run on the synthesized fixture page (normalized coordinates).
class DocLine {
  final String text;
  final double x;
  final double y;
  final double size;
  final bool bold;
  final bool mono;
  final bool muted;

  DocLine({
    required this.text,
    required this.x,
    required this.y,
    required this.size,
    required this.bold,
    required this.mono,
    required this.muted,
  });

  factory DocLine.fromJson(Map<String, dynamic> j) => DocLine(
    text: j['text'] as String? ?? '',
    x: _d(j['x']),
    y: _d(j['y']),
    size: _d(j['size']),
    bold: j['bold'] as bool? ?? false,
    mono: j['mono'] as bool? ?? false,
    muted: j['muted'] as bool? ?? false,
  );
}

/// A grounded field: where on the page a contract fact's value sits.
class DocRegion {
  final String path;
  final List<double> box; // [x, y, w, h], normalized
  final String value;
  final double confidence;

  DocRegion({
    required this.path,
    required this.box,
    required this.value,
    required this.confidence,
  });

  factory DocRegion.fromJson(Map<String, dynamic> j) => DocRegion(
    path: j['path'] as String,
    box: (j['box'] as List<dynamic>? ?? const []).map(_d).toList(),
    value: j['value'] as String? ?? '',
    confidence: _d(j['confidence']),
  );
}

class CaseDocument {
  final String caseId;
  final String title;
  final String kind; // invoice | expense | contract | generic
  final List<DocLine> lines;
  final List<DocRegion> regions;

  CaseDocument({
    required this.caseId,
    required this.title,
    required this.kind,
    required this.lines,
    required this.regions,
  });

  factory CaseDocument.fromJson(Map<String, dynamic> j) => CaseDocument(
    caseId: j['case_id'] as String,
    title: j['title'] as String? ?? '',
    kind: j['kind'] as String? ?? 'generic',
    lines: (j['lines'] as List<dynamic>? ?? const [])
        .map((l) => DocLine.fromJson(l as Map<String, dynamic>))
        .toList(),
    regions: (j['regions'] as List<dynamic>? ?? const [])
        .map((r) => DocRegion.fromJson(r as Map<String, dynamic>))
        .toList(),
  );
}

/// What applying a recommendation produced: the draft it staged, and the
/// experiment auto-launched to measure it (empty when no golden set exists).
class AppliedRecommendation {
  final FlowVersion draft;
  final String summary;
  final String testRunId;

  AppliedRecommendation({
    required this.draft,
    required this.summary,
    required this.testRunId,
  });

  factory AppliedRecommendation.fromJson(Map<String, dynamic> j) =>
      AppliedRecommendation(
        draft: FlowVersion.fromJson(j['draft'] as Map<String, dynamic>),
        summary: j['summary'] as String? ?? '',
        testRunId: j['test_run_id'] as String? ?? '',
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
  final int goldenCases;
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
    required this.goldenCases,
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
    goldenCases: _i(j['golden_cases']),
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

class RunSummary {
  final String id;
  final String status; // completed | waiting_on_human | escalated | failed
  final String caseRef;
  final DateTime startedAt;
  final double activeSeconds;
  final int suspendedMinutes;
  final double costUsd;
  final int modelCalls;
  final int tasksRaised;
  final int actionsCommitted;

  RunSummary({
    required this.id,
    required this.status,
    required this.caseRef,
    required this.startedAt,
    required this.activeSeconds,
    required this.suspendedMinutes,
    required this.costUsd,
    required this.modelCalls,
    required this.tasksRaised,
    required this.actionsCommitted,
  });

  factory RunSummary.fromJson(Map<String, dynamic> j) => RunSummary(
    id: j['id'] as String,
    status: j['status'] as String,
    caseRef: j['case_ref'] as String? ?? '',
    startedAt: _date(j['started_at']),
    activeSeconds: _d(j['active_seconds']),
    suspendedMinutes: _i(j['suspended_minutes']),
    costUsd: _d(j['cost_usd']),
    modelCalls: _i(j['model_calls']),
    tasksRaised: _i(j['tasks_raised']),
    actionsCommitted: _i(j['actions_committed']),
  );
}

class ModelCallRecord {
  final String stepId;
  final String stepName;
  final String model;
  final int inputTokens;
  final int outputTokens;
  final double costUsd;
  final int durationMs;
  final String promptPreview;
  final String rawResponse;
  final String promptVersion;
  final double temperature;

  ModelCallRecord({
    required this.stepId,
    required this.stepName,
    required this.model,
    required this.inputTokens,
    required this.outputTokens,
    required this.costUsd,
    required this.durationMs,
    required this.promptPreview,
    required this.rawResponse,
    required this.promptVersion,
    required this.temperature,
  });

  factory ModelCallRecord.fromJson(Map<String, dynamic> j) => ModelCallRecord(
    stepId: j['step_id'] as String? ?? '',
    stepName: j['step_name'] as String? ?? '',
    model: j['model'] as String? ?? '',
    inputTokens: _i(j['input_tokens']),
    outputTokens: _i(j['output_tokens']),
    costUsd: _d(j['cost_usd']),
    durationMs: _i(j['duration_ms']),
    promptPreview: j['prompt_preview'] as String? ?? '',
    rawResponse: j['raw_response'] as String? ?? '',
    promptVersion: j['prompt_version'] as String? ?? '',
    temperature: _d(j['temperature']),
  );
}

class RunStepEvent {
  final String stepId;
  final String type;
  final String name;
  final String status; // completed | waiting | failed | skipped
  final DateTime at;
  final String detail;
  final String verdict;
  final String queue;
  final String decidedBy;
  final int suspendedMinutes;
  final String ledgerRef;
  final String inputPreview;
  final String outputPreview;

  RunStepEvent({
    required this.stepId,
    required this.type,
    required this.name,
    required this.status,
    required this.at,
    required this.detail,
    required this.verdict,
    required this.queue,
    required this.decidedBy,
    required this.suspendedMinutes,
    required this.ledgerRef,
    required this.inputPreview,
    required this.outputPreview,
  });

  factory RunStepEvent.fromJson(Map<String, dynamic> j) => RunStepEvent(
    stepId: j['step_id'] as String? ?? '',
    type: j['type'] as String,
    name: j['name'] as String,
    status: j['status'] as String,
    at: _date(j['at']),
    detail: j['detail'] as String? ?? '',
    verdict: j['verdict'] as String? ?? '',
    queue: j['queue'] as String? ?? '',
    decidedBy: j['decided_by'] as String? ?? '',
    suspendedMinutes: _i(j['suspended_minutes']),
    ledgerRef: j['ledger_ref'] as String? ?? '',
    inputPreview: j['input_preview'] as String? ?? '',
    outputPreview: j['output_preview'] as String? ?? '',
  );
}

class RunDetail extends RunSummary {
  final String triggerEvent;
  final int tokensIn;
  final int tokensOut;
  final List<RunStepEvent> steps;
  final List<ModelCallRecord> calls;

  RunDetail({
    required RunSummary base,
    required this.triggerEvent,
    required this.tokensIn,
    required this.tokensOut,
    required this.steps,
    required this.calls,
  }) : super(
         id: base.id,
         status: base.status,
         caseRef: base.caseRef,
         startedAt: base.startedAt,
         activeSeconds: base.activeSeconds,
         suspendedMinutes: base.suspendedMinutes,
         costUsd: base.costUsd,
         modelCalls: base.modelCalls,
         tasksRaised: base.tasksRaised,
         actionsCommitted: base.actionsCommitted,
       );

  factory RunDetail.fromJson(Map<String, dynamic> j) => RunDetail(
    base: RunSummary.fromJson(j),
    triggerEvent: j['trigger_event'] as String? ?? '',
    tokensIn: _i(j['tokens_in']),
    tokensOut: _i(j['tokens_out']),
    steps: (j['steps'] as List<dynamic>? ?? const [])
        .map((s) => RunStepEvent.fromJson(s as Map<String, dynamic>))
        .toList(),
    calls: (j['calls'] as List<dynamic>? ?? const [])
        .map((s) => ModelCallRecord.fromJson(s as Map<String, dynamic>))
        .toList(),
  );
}

class AuditEntry {
  final DateTime at;
  final String kind; // decision | action | resolution | knowledge
  final String actor;
  final String summary;
  final String runId;
  final String ledgerRef;

  AuditEntry({
    required this.at,
    required this.kind,
    required this.actor,
    required this.summary,
    required this.runId,
    required this.ledgerRef,
  });

  factory AuditEntry.fromJson(Map<String, dynamic> j) => AuditEntry(
    at: _date(j['at']),
    kind: j['kind'] as String,
    actor: j['actor'] as String? ?? '',
    summary: j['summary'] as String? ?? '',
    runId: j['run_id'] as String? ?? '',
    ledgerRef: j['ledger_ref'] as String? ?? '',
  );
}

class GoldenCase {
  final String id;
  final String name;
  final String inputRef;
  final String expected;
  // correction-set | exemplar | human-authored | promoted-from-run
  final String source;
  final String addedBy;
  final DateTime addedAt;
  final String runId;
  final String payload;
  final String documentRef;
  final List<FactAssertion> assertions;
  final List<TrajectoryAssertion> trajectory;
  final List<PinnedRead> readSet;
  final String reviewedBy;

  GoldenCase({
    required this.id,
    required this.name,
    required this.inputRef,
    required this.expected,
    required this.source,
    required this.addedBy,
    required this.addedAt,
    required this.runId,
    required this.payload,
    required this.documentRef,
    required this.assertions,
    required this.trajectory,
    required this.readSet,
    required this.reviewedBy,
  });

  int get assertionCount => assertions.length + trajectory.length;

  factory GoldenCase.fromJson(Map<String, dynamic> j) => GoldenCase(
    id: j['id'] as String,
    name: j['name'] as String,
    inputRef: j['input_ref'] as String? ?? '',
    expected: j['expected'] as String? ?? '',
    source: j['source'] as String? ?? 'human-authored',
    addedBy: j['added_by'] as String? ?? '',
    addedAt: _date(j['added_at']),
    runId: j['run_id'] as String? ?? '',
    payload: j['payload'] as String? ?? '',
    documentRef: j['document_ref'] as String? ?? '',
    assertions: (j['assertions'] as List<dynamic>? ?? const [])
        .map((a) => FactAssertion.fromJson(a as Map<String, dynamic>))
        .toList(),
    trajectory: (j['trajectory'] as List<dynamic>? ?? const [])
        .map((t) => TrajectoryAssertion.fromJson(t as Map<String, dynamic>))
        .toList(),
    readSet: (j['read_set'] as List<dynamic>? ?? const [])
        .map((r) => PinnedRead.fromJson(r as Map<String, dynamic>))
        .toList(),
    reviewedBy: j['reviewed_by'] as String? ?? '',
  );
}

/// One assertion, graded for one (case, version) pair, localized to the step
/// that owns the fact in that version's producer map.
class AssertionResult {
  final String kind; // fact | trajectory
  final String label;
  final String op;
  final String expected;
  final String actual;
  final String verdict; // pass | fail | review
  final String grader; // deterministic | judge | human
  final String stepId;
  final String? baselineVerdict;

  AssertionResult({
    required this.kind,
    required this.label,
    required this.op,
    required this.expected,
    required this.actual,
    required this.verdict,
    required this.grader,
    required this.stepId,
    required this.baselineVerdict,
  });

  bool get passed => verdict == 'pass';

  factory AssertionResult.fromJson(Map<String, dynamic> j) => AssertionResult(
    kind: j['kind'] as String? ?? 'fact',
    label: j['label'] as String? ?? '',
    op: j['op'] as String? ?? 'equals',
    expected: j['expected'] as String? ?? '',
    actual: j['actual'] as String? ?? '',
    verdict: j['verdict'] as String? ?? 'pass',
    grader: j['grader'] as String? ?? 'deterministic',
    stepId: j['step_id'] as String? ?? '',
    baselineVerdict: j['baseline_verdict'] as String?,
  );
}

/// Assertion pass/fail totals for the assertions one step owns — the number
/// the canvas eval lens paints on that node.
class StepStat {
  final String stepId;
  final String stepName;
  final String stepType;
  final int passed;
  final int failed;
  final int baselinePassed;
  final int baselineFailed;

  StepStat({
    required this.stepId,
    required this.stepName,
    required this.stepType,
    required this.passed,
    required this.failed,
    required this.baselinePassed,
    required this.baselineFailed,
  });

  int get total => passed + failed;
  double get passRate => total == 0 ? 1 : passed / total;
  double get baselinePassRate {
    final t = baselinePassed + baselineFailed;
    return t == 0 ? 1 : baselinePassed / t;
  }

  factory StepStat.fromJson(Map<String, dynamic> j) => StepStat(
    stepId: j['step_id'] as String,
    stepName: j['step_name'] as String? ?? '',
    stepType: j['step_type'] as String? ?? '',
    passed: _i(j['passed']),
    failed: _i(j['failed']),
    baselinePassed: _i(j['baseline_passed']),
    baselineFailed: _i(j['baseline_failed']),
  );
}

/// The promotion gate, stated rather than implied.
class GateResult {
  final bool passed;
  final List<String> reasons;

  GateResult({required this.passed, required this.reasons});

  factory GateResult.fromJson(Map<String, dynamic> j) => GateResult(
    passed: j['passed'] as bool? ?? false,
    reasons: _strings(j['reasons']),
  );
}

class TestCaseResult {
  final String caseId;
  final String name;
  final bool passed;
  final String expected;
  final String actual;
  final double costUsd;
  final int durationMs;
  final bool? baselinePassed;
  final double baselineCostUsd;
  final int baselineDurationMs;
  final List<AssertionResult> assertions;

  TestCaseResult({
    required this.caseId,
    required this.name,
    required this.passed,
    required this.expected,
    required this.actual,
    required this.costUsd,
    required this.durationMs,
    required this.baselinePassed,
    required this.baselineCostUsd,
    required this.baselineDurationMs,
    required this.assertions,
  });

  /// The baseline handled this case and the candidate does not — the one
  /// outcome that should block promoting a draft.
  bool get regressed => baselinePassed == true && !passed;

  /// The candidate fixed something the baseline got wrong.
  bool get improved => baselinePassed == false && passed;

  factory TestCaseResult.fromJson(Map<String, dynamic> j) => TestCaseResult(
    caseId: j['case_id'] as String,
    name: j['name'] as String? ?? '',
    passed: j['passed'] as bool? ?? false,
    expected: j['expected'] as String? ?? '',
    actual: j['actual'] as String? ?? '',
    costUsd: _d(j['cost_usd']),
    durationMs: _i(j['duration_ms']),
    baselinePassed: j['baseline_passed'] as bool?,
    baselineCostUsd: _d(j['baseline_cost_usd']),
    baselineDurationMs: _i(j['baseline_duration_ms']),
    assertions: (j['assertions'] as List<dynamic>? ?? const [])
        .map((a) => AssertionResult.fromJson(a as Map<String, dynamic>))
        .toList(),
  );
}

class TestRun {
  final String id;
  final String flowId;
  final String candidateVersion;
  final String baselineVersion;
  final String status; // running | completed
  final String launchedBy;
  final DateTime launchedAt;
  final DateTime? finishedAt;
  final int totalCases;
  final List<TestCaseResult> results;
  final int passed;
  final double costUsd;
  final int baselinePassed;
  final double baselineCostUsd;
  final double costDeltaUsd;
  final int latencyDeltaMs;
  final int regressions;
  final bool writesSuppressed;
  final int datasetVersion;
  final String reads; // pinned | live
  final List<StepStat> stepStats;
  final GateResult? gate;
  final int reviewsRaised;
  final String launchedFor;

  TestRun({
    required this.id,
    required this.flowId,
    required this.candidateVersion,
    required this.baselineVersion,
    required this.status,
    required this.launchedBy,
    required this.launchedAt,
    required this.finishedAt,
    required this.totalCases,
    required this.results,
    required this.passed,
    required this.costUsd,
    required this.baselinePassed,
    required this.baselineCostUsd,
    required this.costDeltaUsd,
    required this.latencyDeltaMs,
    required this.regressions,
    required this.writesSuppressed,
    required this.datasetVersion,
    required this.reads,
    required this.stepStats,
    required this.gate,
    required this.reviewsRaised,
    required this.launchedFor,
  });

  bool get running => status == 'running';
  double get passRate => results.isEmpty ? 0 : passed / results.length;
  double get baselinePassRate =>
      results.isEmpty ? 0 : baselinePassed / results.length;

  factory TestRun.fromJson(Map<String, dynamic> j) => TestRun(
    id: j['id'] as String,
    flowId: j['flow_id'] as String,
    candidateVersion: j['candidate_version'] as String? ?? '',
    baselineVersion: j['baseline_version'] as String? ?? '',
    status: j['status'] as String? ?? 'running',
    launchedBy: j['launched_by'] as String? ?? '',
    launchedAt: _date(j['launched_at']),
    finishedAt: j['finished_at'] == null ? null : _date(j['finished_at']),
    totalCases: _i(j['total_cases']),
    results: (j['results'] as List<dynamic>? ?? const [])
        .map((s) => TestCaseResult.fromJson(s as Map<String, dynamic>))
        .toList(),
    passed: _i(j['passed']),
    costUsd: _d(j['cost_usd']),
    baselinePassed: _i(j['baseline_passed']),
    baselineCostUsd: _d(j['baseline_cost_usd']),
    costDeltaUsd: _d(j['cost_delta_usd']),
    latencyDeltaMs: _i(j['latency_delta_ms']),
    regressions: _i(j['regressions']),
    writesSuppressed: j['writes_suppressed'] as bool? ?? true,
    datasetVersion: _i(j['dataset_version']),
    reads: j['reads'] as String? ?? 'pinned',
    stepStats: (j['step_stats'] as List<dynamic>? ?? const [])
        .map((s) => StepStat.fromJson(s as Map<String, dynamic>))
        .toList(),
    gate: j['gate'] == null
        ? null
        : GateResult.fromJson(j['gate'] as Map<String, dynamic>),
    reviewsRaised: _i(j['reviews_raised']),
    launchedFor: j['launched_for'] as String? ?? '',
  );
}
