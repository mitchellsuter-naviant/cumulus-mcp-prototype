/// Durable Flow runs. Mirrors `server/app/runs.py`.
///
/// A run is the record of what happened, step by step, with the read-set each
/// step used. The journal a workspace's run list shows and the journal a
/// suspended run resumes from are the same object — which is the point.
library;

class RunStep {
  final String stepId;
  final String name;
  final String kind;
  final String status; // done | suspended | failed
  final String summary;

  /// Whatever the step returned, JSON-shaped. Replay hands this back instead
  /// of calling the step again.
  final dynamic output;
  final List<String> refs;

  /// What this step *wrote*: object, link, document and knowledge refs. Runs
  /// have always recorded what they read; this is the half an auditor asks
  /// about, and it is what lets a view replay the graph assembling itself.
  final List<String> writes;
  final String error;
  final int attempts;
  final String atIso;
  final int durationMs;
  final String taskId;
  final Map<String, dynamic>? decision;
  final String model;
  final double costUsd;

  const RunStep({
    required this.stepId,
    required this.name,
    required this.kind,
    required this.status,
    required this.summary,
    required this.output,
    required this.refs,
    required this.writes,
    required this.error,
    required this.attempts,
    required this.atIso,
    required this.durationMs,
    required this.taskId,
    required this.decision,
    required this.model,
    required this.costUsd,
  });

  bool get isGate => kind == 'task';
  bool get isModelCall => kind == 'modelcall';

  factory RunStep.fromJson(Map<String, dynamic> json) => RunStep(
    stepId: json['step_id'] as String? ?? '',
    name: json['name'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    status: json['status'] as String? ?? 'done',
    summary: json['summary'] as String? ?? '',
    output: json['output'],
    refs: (json['refs'] as List<dynamic>? ?? const []).map((e) => '$e').toList(),
    writes: (json['writes'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    error: json['error'] as String? ?? '',
    attempts: json['attempts'] as int? ?? 1,
    atIso: json['at_iso'] as String? ?? '',
    durationMs: json['duration_ms'] as int? ?? 0,
    taskId: json['task_id'] as String? ?? '',
    decision: json['decision'] as Map<String, dynamic>?,
    model: json['model'] as String? ?? '',
    costUsd: (json['cost_usd'] as num? ?? 0).toDouble(),
  );
}

/// The definition a run executed, or the absence of a claim.
///
/// The engine pins the version at start and honours it on resume, so a run is
/// evidence about *that* graph and no other. A record written before the
/// field carries 0, which is not version zero — it is silence, and the console
/// says so rather than putting the run on whatever is active today.
mixin RanUnderVersion {
  int get flowVersion;

  bool get claimsVersion => flowVersion > 0;
}

class FlowRun with RanUnderVersion {
  final String id;
  final String flowId;

  @override
  final int flowVersion;
  final String status; // running | suspended | succeeded | failed
  final String caseRef;
  final String actor;
  final Map<String, dynamic> inputs;
  final Map<String, dynamic> outputs;
  final List<RunStep> steps;
  final List<String> refs;
  final String openTaskId;
  final String suspendedAtStep;
  final int resumeCount;
  final String error;
  final int modelCalls;
  final double costUsd;
  final String startedAtIso;
  final String finishedAtIso;

  const FlowRun({
    required this.id,
    required this.flowId,
    this.flowVersion = 0,
    required this.status,
    required this.caseRef,
    required this.actor,
    required this.inputs,
    required this.outputs,
    required this.steps,
    required this.refs,
    required this.openTaskId,
    required this.suspendedAtStep,
    required this.resumeCount,
    required this.error,
    required this.modelCalls,
    required this.costUsd,
    required this.startedAtIso,
    required this.finishedAtIso,
  });

  /// Gates this run stopped at — how many times a person was asked.
  int get humanRounds => steps.where((s) => s.isGate).length;

  /// Everything this run wrote, in the order the steps wrote it.
  List<String> get writes {
    final out = <String>[];
    for (final step in steps) {
      for (final ref in step.writes) {
        if (!out.contains(ref)) out.add(ref);
      }
    }
    return out;
  }

  int get durationMs => steps.fold(0, (sum, s) => sum + s.durationMs);

  factory FlowRun.fromJson(Map<String, dynamic> json) => FlowRun(
    id: json['id'] as String? ?? '',
    flowId: json['flow_id'] as String? ?? '',
    flowVersion: json['flow_version'] as int? ?? 0,
    status: json['status'] as String? ?? '',
    caseRef: json['case_ref'] as String? ?? '',
    actor: json['actor'] as String? ?? '',
    inputs: (json['inputs'] as Map<String, dynamic>?) ?? const {},
    outputs: (json['outputs'] as Map<String, dynamic>?) ?? const {},
    steps: (json['steps'] as List<dynamic>? ?? const [])
        .map((e) => RunStep.fromJson(e as Map<String, dynamic>))
        .toList(),
    refs: (json['refs'] as List<dynamic>? ?? const []).map((e) => '$e').toList(),
    openTaskId: json['open_task_id'] as String? ?? '',
    suspendedAtStep: json['suspended_at_step'] as String? ?? '',
    resumeCount: json['resume_count'] as int? ?? 0,
    error: json['error'] as String? ?? '',
    modelCalls: json['model_calls'] as int? ?? 0,
    costUsd: (json['cost_usd'] as num? ?? 0).toDouble(),
    startedAtIso: json['started_at_iso'] as String? ?? '',
    finishedAtIso: json['finished_at_iso'] as String? ?? '',
  );
}

/// A run as a list row: enough to triage without loading the journal.
class FlowRunSummary with RanUnderVersion {
  final String id;
  final String flowId;

  @override
  final int flowVersion;
  final String status;
  final String caseRef;
  final String actor;
  final int steps;
  final int modelCalls;
  final double costUsd;
  final int durationMs;
  final String openTaskId;
  final String suspendedAtStep;
  final int resumeCount;
  final String error;
  final String startedAtIso;
  final String finishedAtIso;
  final List<String> refs;
  final Map<String, dynamic> outputs;

  const FlowRunSummary({
    required this.id,
    required this.flowId,
    this.flowVersion = 0,
    required this.status,
    required this.caseRef,
    required this.actor,
    required this.steps,
    required this.modelCalls,
    required this.costUsd,
    required this.durationMs,
    required this.openTaskId,
    required this.suspendedAtStep,
    required this.resumeCount,
    required this.error,
    required this.startedAtIso,
    required this.finishedAtIso,
    required this.refs,
    required this.outputs,
  });

  factory FlowRunSummary.fromJson(Map<String, dynamic> json) => FlowRunSummary(
    id: json['id'] as String? ?? '',
    flowId: json['flow_id'] as String? ?? '',
    flowVersion: json['flow_version'] as int? ?? 0,
    status: json['status'] as String? ?? '',
    caseRef: json['case_ref'] as String? ?? '',
    actor: json['actor'] as String? ?? '',
    steps: json['steps'] as int? ?? 0,
    modelCalls: json['model_calls'] as int? ?? 0,
    costUsd: (json['cost_usd'] as num? ?? 0).toDouble(),
    durationMs: json['duration_ms'] as int? ?? 0,
    openTaskId: json['open_task_id'] as String? ?? '',
    suspendedAtStep: json['suspended_at_step'] as String? ?? '',
    resumeCount: json['resume_count'] as int? ?? 0,
    error: json['error'] as String? ?? '',
    startedAtIso: json['started_at_iso'] as String? ?? '',
    finishedAtIso: json['finished_at_iso'] as String? ?? '',
    refs: (json['refs'] as List<dynamic>? ?? const []).map((e) => '$e').toList(),
    outputs: (json['outputs'] as Map<String, dynamic>?) ?? const {},
  );
}

class RunStats {
  final int total;
  final int succeeded;
  final int suspended;
  final int failed;
  final int running;
  final int modelCalls;
  final double costUsd;
  final int withHuman;
  final int humanRounds;
  final Map<String, int> byFlow;

  const RunStats({
    required this.total,
    required this.succeeded,
    required this.suspended,
    required this.failed,
    required this.running,
    required this.modelCalls,
    required this.costUsd,
    required this.withHuman,
    required this.humanRounds,
    required this.byFlow,
  });

  static const empty = RunStats(
    total: 0, succeeded: 0, suspended: 0, failed: 0, running: 0,
    modelCalls: 0, costUsd: 0, withHuman: 0, humanRounds: 0, byFlow: {},
  );

  /// The share of runs that finished with nobody looking at them.
  double get autonomousRate =>
      total == 0 ? 0 : (total - withHuman) / total;

  factory RunStats.fromJson(Map<String, dynamic> json) => RunStats(
    total: json['total'] as int? ?? 0,
    succeeded: json['succeeded'] as int? ?? 0,
    suspended: json['suspended'] as int? ?? 0,
    failed: json['failed'] as int? ?? 0,
    running: json['running'] as int? ?? 0,
    modelCalls: json['model_calls'] as int? ?? 0,
    costUsd: (json['cost_usd'] as num? ?? 0).toDouble(),
    withHuman: json['with_human'] as int? ?? 0,
    humanRounds: json['human_rounds'] as int? ?? 0,
    byFlow: (json['by_flow'] as Map<String, dynamic>? ?? const {})
        .map((k, v) => MapEntry(k, v as int)),
  );
}
