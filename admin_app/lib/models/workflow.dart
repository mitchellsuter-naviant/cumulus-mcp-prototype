/// Models for the Workflows section — the registry and observability plane
/// for the two kinds of authored logic (Workflow and DataFlow).
///
/// Mirrors `server/app/workflows_api.py`. Costs come from audited ModelCall
/// records; the run journal and audit entries are the governance trail.
library;

double _d(dynamic v) => v == null ? 0 : (v as num).toDouble();
int _i(dynamic v) => v == null ? 0 : (v as num).toInt();

List<String> _strings(dynamic v) =>
    (v as List<dynamic>? ?? const []).map((e) => e.toString()).toList();

DateTime _date(dynamic v) =>
    DateTime.tryParse(v?.toString() ?? '')?.toLocal() ?? DateTime.now();

class WorkflowStep {
  final String id;
  final String type; // trigger | modelcall | rule | task | action | event | transform
  final String name;
  final String summary;
  final Map<String, String> config;
  final List<String> next;
  final List<String> branchLabels;

  WorkflowStep({
    required this.id,
    required this.type,
    required this.name,
    required this.summary,
    required this.config,
    required this.next,
    required this.branchLabels,
  });

  factory WorkflowStep.fromJson(Map<String, dynamic> j) => WorkflowStep(
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
class WorkflowVersion {
  final int version;
  final String label;
  final String status; // active | archived | draft
  final List<WorkflowStep> steps;
  final double modelBudgetUsdPerRun;
  final String notes;
  final String authoredBy;
  final DateTime createdAt;

  WorkflowVersion({
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

  factory WorkflowVersion.fromJson(Map<String, dynamic> j) => WorkflowVersion(
    version: _i(j['version']),
    label: j['label'] as String? ?? '',
    status: j['status'] as String? ?? 'archived',
    steps: (j['steps'] as List<dynamic>? ?? const [])
        .map((s) => WorkflowStep.fromJson(s as Map<String, dynamic>))
        .toList(),
    modelBudgetUsdPerRun: _d(j['model_budget_usd_per_run']),
    notes: j['notes'] as String? ?? '',
    authoredBy: j['authored_by'] as String? ?? '',
    createdAt: _date(j['created_at']),
  );
}

/// A step annotated against the version being compared with.
class StepDiff {
  final WorkflowStep step;
  final String change; // unchanged | added | removed | modified
  final List<String> changedFields;

  StepDiff({
    required this.step,
    required this.change,
    required this.changedFields,
  });

  factory StepDiff.fromJson(Map<String, dynamic> j) => StepDiff(
    step: WorkflowStep.fromJson(j['step'] as Map<String, dynamic>),
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

/// The golden set plus where its seeded half came from in the repository.
class GoldenSetInfo {
  final List<GoldenCase> cases;
  final String syncedFrom;
  final DateTime? syncedAt;

  GoldenSetInfo({
    required this.cases,
    required this.syncedFrom,
    required this.syncedAt,
  });

  factory GoldenSetInfo.fromJson(Map<String, dynamic> j) => GoldenSetInfo(
    cases: (j['cases'] as List<dynamic>? ?? const [])
        .map((c) => GoldenCase.fromJson(c as Map<String, dynamic>))
        .toList(),
    syncedFrom: j['synced_from'] as String? ?? '',
    syncedAt: (j['synced_at'] as String? ?? '').isEmpty
        ? null
        : _date(j['synced_at']),
  );
}

class WorkflowSummary {
  final String id;
  final String name;
  final String kind; // workflow | dataflow
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

  WorkflowSummary({
    required this.id,
    required this.name,
    required this.kind,
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

  factory WorkflowSummary.fromJson(Map<String, dynamic> j) => WorkflowSummary(
    id: j['id'] as String,
    name: j['name'] as String? ?? j['id'] as String,
    kind: j['kind'] as String,
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

class WorkflowDetail extends WorkflowSummary {
  final List<WorkflowStep> steps;
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

  WorkflowDetail({
    required WorkflowSummary base,
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
         kind: base.kind,
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

  factory WorkflowDetail.fromJson(Map<String, dynamic> j) => WorkflowDetail(
    base: WorkflowSummary.fromJson(j),
    steps: (j['steps'] as List<dynamic>? ?? const [])
        .map((s) => WorkflowStep.fromJson(s as Map<String, dynamic>))
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
  final String workflowId;
  final String reason;

  FleetAttention({required this.workflowId, required this.reason});

  factory FleetAttention.fromJson(Map<String, dynamic> j) => FleetAttention(
    workflowId: j['workflow_id'] as String,
    reason: j['reason'] as String? ?? '',
  );
}

class FleetStats {
  final int days;
  final int definitions;
  final int active;
  final int workflows;
  final int dataflows;
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
    required this.workflows,
    required this.dataflows,
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
    workflows: _i(j['workflows']),
    dataflows: _i(j['dataflows']),
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

class WorkflowUsage {
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

  WorkflowUsage({
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

  factory WorkflowUsage.fromJson(Map<String, dynamic> j) => WorkflowUsage(
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
  });

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
  );
}

class TestRun {
  final String id;
  final String workflowId;
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

  TestRun({
    required this.id,
    required this.workflowId,
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
  });

  bool get running => status == 'running';
  double get passRate => results.isEmpty ? 0 : passed / results.length;
  double get baselinePassRate =>
      results.isEmpty ? 0 : baselinePassed / results.length;

  factory TestRun.fromJson(Map<String, dynamic> j) => TestRun(
    id: j['id'] as String,
    workflowId: j['workflow_id'] as String,
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
  );
}
