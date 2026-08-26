/// Graph-shaped evals. Mirrors `server/app/evals.py` and `eval_specs.py`.
///
/// A spec is an **overlay on the enterprise model**: the ontology says what an
/// Invoice is; the spec says which parts of it a given flow is answerable for,
/// how tightly to compare them, and at which point in a run to look. Cases
/// supply only the values.
library;

class FieldRule {
  final String field;

  /// exact · approx · fuzzy · one_of · present · absent · ignore
  final String match;
  final double tolerance;
  final List<String> options;
  final String note;

  const FieldRule({
    required this.field,
    required this.match,
    required this.tolerance,
    required this.options,
    required this.note,
  });

  bool get ignored => match == 'ignore';

  /// `approx ±0.01`, `fuzzy ≥0.90`, `one_of (a, b)`.
  String get label => switch (match) {
    'approx' => 'approx ±${tolerance == 0 ? 0.01 : tolerance}',
    'fuzzy' => 'fuzzy ≥${tolerance == 0 ? 0.85 : tolerance}',
    'one_of' => 'one of (${options.join(", ")})',
    _ => match,
  };

  factory FieldRule.fromJson(Map<String, dynamic> json) => FieldRule(
    field: json['field'] as String? ?? '',
    match: json['match'] as String? ?? 'exact',
    tolerance: (json['tolerance'] as num? ?? 0).toDouble(),
    options: (json['options'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    note: json['note'] as String? ?? '',
  );
}

class LinkRule {
  final String type;
  final String direction;
  final String toType;
  final String expect; // exists | count | absent
  final List<FieldRule> props;
  final String note;

  const LinkRule({
    required this.type,
    required this.direction,
    required this.toType,
    required this.expect,
    required this.props,
    required this.note,
  });

  factory LinkRule.fromJson(Map<String, dynamic> json) => LinkRule(
    type: json['type'] as String? ?? '',
    direction: json['direction'] as String? ?? 'out',
    toType: json['to_type'] as String? ?? '',
    expect: json['expect'] as String? ?? 'exists',
    props: (json['props'] as List<dynamic>? ?? const [])
        .map((e) => FieldRule.fromJson(e as Map<String, dynamic>))
        .toList(),
    note: json['note'] as String? ?? '',
  );
}

class ObjectRule {
  final String type;
  final List<FieldRule> fields;
  final List<LinkRule> links;
  final String unlistedFields;

  const ObjectRule({
    required this.type,
    required this.fields,
    required this.links,
    required this.unlistedFields,
  });

  factory ObjectRule.fromJson(Map<String, dynamic> json) => ObjectRule(
    type: json['type'] as String? ?? '',
    fields: (json['fields'] as List<dynamic>? ?? const [])
        .map((e) => FieldRule.fromJson(e as Map<String, dynamic>))
        .toList(),
    links: (json['links'] as List<dynamic>? ?? const [])
        .map((e) => LinkRule.fromJson(e as Map<String, dynamic>))
        .toList(),
    unlistedFields: json['unlisted_fields'] as String? ?? 'ignore',
  );
}

class TrajectoryRule {
  final String kind;
  final String expected;
  final String note;

  const TrajectoryRule({
    required this.kind,
    required this.expected,
    required this.note,
  });

  factory TrajectoryRule.fromJson(Map<String, dynamic> json) => TrajectoryRule(
    kind: json['kind'] as String? ?? '',
    expected: json['expected'] as String? ?? '',
    note: json['note'] as String? ?? '',
  );
}

class EvalCheckpoint {
  final String id;

  /// A step id, or `final`. Evaluating mid-run is what lets a spec assert an
  /// *ordering* — "the vendor is settled before anything is extracted".
  final String afterStep;
  final String label;
  final List<ObjectRule> objects;
  final List<TrajectoryRule> trajectory;
  final String note;

  const EvalCheckpoint({
    required this.id,
    required this.afterStep,
    required this.label,
    required this.objects,
    required this.trajectory,
    required this.note,
  });

  factory EvalCheckpoint.fromJson(Map<String, dynamic> json) => EvalCheckpoint(
    id: json['id'] as String? ?? '',
    afterStep: json['after_step'] as String? ?? 'final',
    label: json['label'] as String? ?? '',
    objects: (json['objects'] as List<dynamic>? ?? const [])
        .map((e) => ObjectRule.fromJson(e as Map<String, dynamic>))
        .toList(),
    trajectory: (json['trajectory'] as List<dynamic>? ?? const [])
        .map((e) => TrajectoryRule.fromJson(e as Map<String, dynamic>))
        .toList(),
    note: json['note'] as String? ?? '',
  );
}

class EvalSpec {
  final String id;
  final String flowId;
  final int version;
  final String summary;
  final List<String> ignoreFields;
  final List<EvalCheckpoint> checkpoints;

  const EvalSpec({
    required this.id,
    required this.flowId,
    required this.version,
    required this.summary,
    required this.ignoreFields,
    required this.checkpoints,
  });

  factory EvalSpec.fromJson(Map<String, dynamic> json) => EvalSpec(
    id: json['id'] as String? ?? '',
    flowId: json['flow_id'] as String? ?? '',
    version: json['version'] as int? ?? 1,
    summary: json['summary'] as String? ?? '',
    ignoreFields: (json['ignore_fields'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    checkpoints: (json['checkpoints'] as List<dynamic>? ?? const [])
        .map((e) => EvalCheckpoint.fromJson(e as Map<String, dynamic>))
        .toList(),
  );
}

class EvalSpecSummary {
  final String id;
  final String flowId;
  final int version;
  final String summary;
  final int checkpoints;
  final int cases;
  final int gradedFields;
  final int ignoredFields;
  final int gradedLinks;
  final String assetId;

  const EvalSpecSummary({
    required this.id,
    required this.flowId,
    required this.version,
    required this.summary,
    required this.checkpoints,
    required this.cases,
    required this.gradedFields,
    required this.ignoredFields,
    required this.gradedLinks,
    required this.assetId,
  });

  factory EvalSpecSummary.fromJson(Map<String, dynamic> json) =>
      EvalSpecSummary(
        id: json['id'] as String? ?? '',
        flowId: json['flow_id'] as String? ?? '',
        version: json['version'] as int? ?? 1,
        summary: json['summary'] as String? ?? '',
        checkpoints: json['checkpoints'] as int? ?? 0,
        cases: json['cases'] as int? ?? 0,
        gradedFields: json['graded_fields'] as int? ?? 0,
        ignoredFields: json['ignored_fields'] as int? ?? 0,
        gradedLinks: json['graded_links'] as int? ?? 0,
        assetId: json['asset_id'] as String? ?? '',
      );
}

class EvalCase {
  final String id;
  final String name;
  final String summary;
  final int deliveries;
  final int fixtureObjects;
  final int decisions;
  final List<String> checkpoints;

  const EvalCase({
    required this.id,
    required this.name,
    required this.summary,
    required this.deliveries,
    required this.fixtureObjects,
    required this.decisions,
    required this.checkpoints,
  });

  factory EvalCase.fromJson(Map<String, dynamic> json) {
    final fixture = (json['fixture'] as Map<String, dynamic>?) ?? const {};
    return EvalCase(
      id: json['id'] as String? ?? '',
      name: json['name'] as String? ?? '',
      summary: json['summary'] as String? ?? '',
      deliveries: (fixture['deliveries'] as List<dynamic>? ?? const []).length,
      fixtureObjects: (fixture['objects'] as List<dynamic>? ?? const []).length,
      decisions: (json['decisions'] as List<dynamic>? ?? const []).length,
      checkpoints: (json['expectations'] as List<dynamic>? ?? const [])
          .map((e) => '${(e as Map<String, dynamic>)['checkpoint']}')
          .toList(),
    );
  }
}

class EvalAssertion {
  final String checkpoint;
  final String target;
  final String field;
  final String rule;
  final String expected;
  final String actual;
  final bool passed;
  final String detail;

  const EvalAssertion({
    required this.checkpoint,
    required this.target,
    required this.field,
    required this.rule,
    required this.expected,
    required this.actual,
    required this.passed,
    required this.detail,
  });

  factory EvalAssertion.fromJson(Map<String, dynamic> json) => EvalAssertion(
    checkpoint: json['checkpoint'] as String? ?? '',
    target: json['target'] as String? ?? '',
    field: json['field'] as String? ?? '',
    rule: json['rule'] as String? ?? '',
    expected: json['expected'] as String? ?? '',
    actual: json['actual'] as String? ?? '',
    passed: json['passed'] as bool? ?? false,
    detail: json['detail'] as String? ?? '',
  );
}

class EvalCaseResult {
  final String caseId;
  final String name;
  final bool passed;
  final String runId;
  final String runStatus;
  final List<EvalAssertion> assertions;
  final List<String> invariants;
  final String error;
  final Map<String, int> knowledgeRevisions;

  const EvalCaseResult({
    required this.caseId,
    required this.name,
    required this.passed,
    required this.runId,
    required this.runStatus,
    required this.assertions,
    required this.invariants,
    required this.error,
    required this.knowledgeRevisions,
  });

  List<EvalAssertion> get failures =>
      assertions.where((a) => !a.passed).toList();

  factory EvalCaseResult.fromJson(Map<String, dynamic> json) => EvalCaseResult(
    caseId: json['case_id'] as String? ?? '',
    name: json['name'] as String? ?? '',
    passed: json['passed'] as bool? ?? false,
    runId: json['run_id'] as String? ?? '',
    runStatus: json['run_status'] as String? ?? '',
    assertions: (json['assertions'] as List<dynamic>? ?? const [])
        .map((e) => EvalAssertion.fromJson(e as Map<String, dynamic>))
        .toList(),
    invariants: (json['invariants'] as List<dynamic>? ?? const [])
        .map((e) {
          final v = e as Map<String, dynamic>;
          return '[${v['kind']}] ${v['subject']}: ${v['detail']}';
        })
        .toList(),
    error: json['error'] as String? ?? '',
    knowledgeRevisions:
        (json['knowledge_revisions'] as Map<String, dynamic>? ?? const {})
            .map((k, v) => MapEntry(k, v as int)),
  );
}

class EvalRunResult {
  final String specId;
  final String flowId;
  final int specVersion;
  final String atIso;
  final String launchedBy;
  final int passed;
  final int failed;
  final int assertions;
  final int failedAssertions;
  final int invariantViolations;
  final List<EvalCaseResult> cases;

  const EvalRunResult({
    required this.specId,
    required this.flowId,
    required this.specVersion,
    required this.atIso,
    required this.launchedBy,
    required this.passed,
    required this.failed,
    required this.assertions,
    required this.failedAssertions,
    required this.invariantViolations,
    required this.cases,
  });

  factory EvalRunResult.fromJson(Map<String, dynamic> json) {
    final result = (json['result'] as Map<String, dynamic>?) ?? const {};
    return EvalRunResult(
      specId: json['spec_id'] as String? ?? '',
      flowId: json['flow_id'] as String? ?? '',
      specVersion: json['spec_version'] as int? ?? 1,
      atIso: json['at_iso'] as String? ?? '',
      launchedBy: json['launched_by'] as String? ?? '',
      passed: json['passed'] as int? ?? 0,
      failed: json['failed'] as int? ?? 0,
      assertions: json['assertions'] as int? ?? 0,
      failedAssertions: json['failed_assertions'] as int? ?? 0,
      invariantViolations: json['invariant_violations'] as int? ?? 0,
      cases: (result['cases'] as List<dynamic>? ?? const [])
          .map((e) => EvalCaseResult.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}
