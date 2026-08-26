import 'package:flutter/material.dart';

import '../models/eval.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// A flow's eval spec, and the result of running it.
///
/// The spec is the interesting artifact, so it is what the panel leads with:
/// an **overlay on the enterprise model** saying which parts of a shared
/// `Invoice` or `ExpenseRequest` this flow is answerable for, how tightly to
/// compare each field, which edges matter, and — through checkpoints — at
/// which point in a run to look. Reading it answers "what does this flow
/// promise" without inferring it from fifty cases.
///
/// Two things are deliberately prominent because they are the parts people
/// get wrong:
///
/// - **`ignore` is a decision.** A due date is the vendor's payment terms, so
///   grading it would be grading the wrong system. Ignored fields are shown,
///   not hidden, so the choice is reviewable.
/// - **A checkpoint is an ordering promise.** "No extraction before the vendor
///   is resolved" is a claim about the *path*, and it is asserted at `z1`
///   rather than inferred from the end state.
class EvalContractPanel extends StatefulWidget {
  final ApiClient api;
  final String flowId;

  const EvalContractPanel({super.key, required this.api, required this.flowId});

  @override
  State<EvalContractPanel> createState() => _EvalContractPanelState();
}

class _EvalContractPanelState extends State<EvalContractPanel> {
  EvalSpecSummary? _summary;
  EvalSpec? _spec;
  List<EvalCase> _cases = const [];
  EvalRunResult? _result;
  String? _error;
  bool _loading = true;
  bool _running = false;
  String? _expandedCase;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final specs = await widget.api.listEvalSpecs(flowId: widget.flowId);
      if (specs.isEmpty) {
        if (mounted) setState(() => _loading = false);
        return;
      }
      final spec = await widget.api.getEvalSpec(specs.first.id);
      final cases = await widget.api.listEvalCases(specs.first.id);
      final last = await widget.api.lastEvalResult(specs.first.id);
      if (!mounted) return;
      setState(() {
        _summary = specs.first;
        _spec = spec;
        _cases = cases;
        _result = last;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _loading = false;
        });
      }
    }
  }

  Future<void> _run() async {
    final summary = _summary;
    if (summary == null) return;
    setState(() {
      _running = true;
      _error = null;
    });
    try {
      final result = await widget.api.runEval(summary.id);
      if (mounted) {
        setState(() {
          _result = result;
          _running = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.statusCode == 403
              ? 'Launching an eval needs admin. (A CI key may do it — that is '
                  'the one write here a credential should make.)'
              : e.message;
          _running = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const CumulusCard(child: CumulusLoading());
    final spec = _spec;
    final summary = _summary;
    if (spec == null || summary == null) {
      return CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.rule_folder_outlined,
          title: 'No eval spec for this flow',
          message:
              'A spec declares what the flow is answerable for and how tightly '
              'to judge it. Specs live in server/app/eval_specs.py and are '
              'exported to the corpus.',
        ),
      );
    }
    final result = _result;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SectionHeader(
                title: 'Eval contract · ${spec.id}',
                subtitle:
                    'v${spec.version} · an overlay on the enterprise model: '
                    'which parts of it this flow establishes, and how tightly',
                trailing: Wrap(
                  spacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    MetaPill('knowledge:${summary.assetId}'),
                    FilledButton.icon(
                      onPressed: _running ? null : _run,
                      icon: _running
                          ? const SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.play_arrow, size: 16),
                      label: Text(
                        _running
                            ? 'Running…'
                            : 'Run ${summary.cases} case(s)',
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 12),
              Text(spec.summary, style: CumulusText.body),
              if (_error != null) ...[
                const SizedBox(height: 12),
                ErrorNotice(message: _error!),
              ],
              const SizedBox(height: 16),
              StatStrip(
                tiles: [
                  StatTile(
                    label: 'Checkpoints',
                    value: '${summary.checkpoints}',
                    caption: 'points in the run it looks at',
                  ),
                  StatTile(
                    label: 'Graded fields',
                    value: '${summary.gradedFields}',
                    caption: '${summary.ignoredFields} ignored on purpose',
                  ),
                  StatTile(
                    label: 'Graded edges',
                    value: '${summary.gradedLinks}',
                    caption: 'the links this flow owns',
                  ),
                  StatTile(
                    label: 'Cases',
                    value: '${summary.cases}',
                    caption: result == null
                        ? 'not run in this process'
                        : '${result.passed} passing',
                  ),
                ],
              ),
            ],
          ),
        ),
        if (result != null) _ResultCard(
          result: result,
          expandedCase: _expandedCase,
          onToggle: (id) => setState(
            () => _expandedCase = _expandedCase == id ? null : id,
          ),
        ),
        for (final checkpoint in spec.checkpoints)
          _CheckpointCard(checkpoint: checkpoint, ignoreFields: spec.ignoreFields),
        _CaseListCard(cases: _cases, result: result),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// The contract
// ---------------------------------------------------------------------------

class _CheckpointCard extends StatelessWidget {
  final EvalCheckpoint checkpoint;
  final List<String> ignoreFields;

  const _CheckpointCard({
    required this.checkpoint,
    required this.ignoreFields,
  });

  @override
  Widget build(BuildContext context) {
    final isFinal = checkpoint.afterStep == 'final';
    return CumulusCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: 'Checkpoint · ${checkpoint.id}',
            subtitle: checkpoint.label,
            trailing: Tooltip(
              message: isFinal
                  ? 'Evaluated however the run ended.'
                  : 'Evaluated as of this step — reconstructed from the claims '
                      'and links whose step came no later, so the assertion is '
                      'about the run at that moment.',
              child: StatusBadge(
                isFinal ? 'at the end' : 'after ${checkpoint.afterStep}',
                tone: isFinal ? CumulusTone.neutral : CumulusTone.accent,
                uppercase: true,
              ),
            ),
          ),
          if (checkpoint.note.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(checkpoint.note, style: CumulusText.meta),
          ],
          for (final rule in checkpoint.objects) ...[
            const SizedBox(height: 16),
            Row(
              children: [
                StatusBadge(rule.type, tone: CumulusTone.teal, dense: true),
                const SizedBox(width: 8),
                if (rule.fields.isEmpty && rule.links.isEmpty)
                  Text(
                    'no field rules — declared so a case can assert the '
                    'object is absent',
                    style: CumulusText.meta,
                  )
                else
                  Text(
                    '${rule.fields.where((f) => !f.ignored).length} graded · '
                    '${rule.fields.where((f) => f.ignored).length} ignored · '
                    '${rule.links.length} edge(s)',
                    style: CumulusText.meta,
                  ),
                if (rule.unlistedFields == 'require') ...[
                  const SizedBox(width: 8),
                  const Tooltip(
                    message:
                        'Any field the flow claims that this spec does not '
                        'name is reported as drift.',
                    child: StatusBadge(
                      'no drift',
                      tone: CumulusTone.warn,
                      dense: true,
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                for (final field in rule.fields) _RulePill(rule: field),
                for (final link in rule.links) _LinkPill(rule: link),
              ],
            ),
          ],
          if (checkpoint.trajectory.isNotEmpty) ...[
            const SizedBox(height: 16),
            const FieldLabel(
              'Trajectory',
              hint: 'claims about the path, scoped to this checkpoint',
            ),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                for (final rule in checkpoint.trajectory)
                  Tooltip(
                    message: rule.note.isEmpty
                        ? 'The case supplies the expected value.'
                        : rule.note,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 9,
                        vertical: 5,
                      ),
                      decoration: BoxDecoration(
                        color: CumulusColors.surfaceMuted,
                        borderRadius: BorderRadius.circular(7),
                      ),
                      child: Text(
                        rule.expected.isEmpty
                            ? '${rule.kind} → per case'
                            : '${rule.kind} ${rule.expected}',
                        style: CumulusText.mono,
                      ),
                    ),
                  ),
              ],
            ),
          ],
          if (ignoreFields.isNotEmpty) ...[
            const SizedBox(height: 14),
            Text(
              'Never graded on any type: ${ignoreFields.join(", ")}.',
              style: CumulusText.statCaption,
            ),
          ],
        ],
      ),
    );
  }
}

class _RulePill extends StatelessWidget {
  final FieldRule rule;

  const _RulePill({required this.rule});

  @override
  Widget build(BuildContext context) {
    final tone = rule.ignored
        ? CumulusTone.neutral
        : switch (rule.match) {
            'exact' => CumulusTone.success,
            'approx' => CumulusTone.teal,
            'fuzzy' => CumulusTone.violet,
            'one_of' => CumulusTone.accent,
            _ => CumulusTone.warn,
          };
    return Tooltip(
      message: rule.note.isEmpty
          ? (rule.ignored
              ? 'Ignored deliberately — grading it would grade another system.'
              : 'Compared with ${rule.label}.')
          : rule.note,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: rule.ignored ? CumulusColors.canvas : tone.background,
          borderRadius: BorderRadius.circular(7),
          border: Border.all(
            color: rule.ignored
                ? CumulusColors.borderStrong
                : Colors.transparent,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              rule.field,
              style: CumulusText.mono.copyWith(
                color: rule.ignored
                    ? CumulusColors.inkMuted
                    : CumulusColors.inkSecondary,
                decoration: rule.ignored ? TextDecoration.lineThrough : null,
              ),
            ),
            const SizedBox(width: 7),
            Text(
              rule.label,
              style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w600,
                color: rule.ignored ? CumulusColors.inkMuted : tone.foreground,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LinkPill extends StatelessWidget {
  final LinkRule rule;

  const _LinkPill({required this.rule});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: rule.note.isEmpty
          ? 'Edge ${rule.type} (${rule.direction}): ${rule.expect}'
          : rule.note,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(7),
          border: Border.all(color: CumulusColors.accent.withValues(alpha: 0.5)),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              rule.direction == 'out' ? Icons.arrow_forward : Icons.arrow_back,
              size: 11,
              color: CumulusColors.accentInk,
            ),
            const SizedBox(width: 5),
            Text(rule.type, style: CumulusText.mono),
            const SizedBox(width: 7),
            Text(
              rule.expect,
              style: const TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w600,
                color: CumulusColors.accentInk,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The result
// ---------------------------------------------------------------------------

class _ResultCard extends StatelessWidget {
  final EvalRunResult result;
  final String? expandedCase;
  final void Function(String caseId) onToggle;

  const _ResultCard({
    required this.result,
    required this.expandedCase,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    final ok = result.failed == 0 && result.invariantViolations == 0;
    final tone = ok ? CumulusTone.success : CumulusTone.danger;
    return ListCard(
      title: 'Last result',
      subtitle:
          '${result.passed}/${result.passed + result.failed} case(s) · '
          '${result.assertions - result.failedAssertions}/${result.assertions} '
          'assertion(s) · ${result.invariantViolations} ontology violation(s) · '
          'ran by ${result.launchedBy.isEmpty ? "someone" : result.launchedBy}',
      headerTrailing: StatusBadge(
        ok ? 'green' : 'failing',
        tone: tone,
        uppercase: true,
      ),
      children: [
        for (final outcome in result.cases) ...[
          CumulusListRow(
            onTap: () => onToggle(outcome.caseId),
            leading: Icon(
              outcome.passed ? Icons.check_circle : Icons.cancel,
              size: 20,
              color: outcome.passed
                  ? CumulusTone.success.foreground
                  : CumulusTone.danger.foreground,
            ),
            title: Row(
              children: [
                Flexible(child: Text(outcome.name)),
                const SizedBox(width: 10),
                StatusBadge(
                  outcome.runStatus.isEmpty ? 'no run' : outcome.runStatus,
                  tone: switch (outcome.runStatus) {
                    'succeeded' => CumulusTone.success,
                    'suspended' => CumulusTone.warn,
                    'failed' => CumulusTone.danger,
                    _ => CumulusTone.neutral,
                  },
                  dense: true,
                ),
                if (outcome.failures.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  StatusBadge(
                    '${outcome.failures.length} failed',
                    tone: CumulusTone.danger,
                    dense: true,
                  ),
                ],
                if (outcome.invariants.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  StatusBadge(
                    '${outcome.invariants.length} ontology',
                    tone: CumulusTone.danger,
                    dense: true,
                  ),
                ],
              ],
            ),
            subtitle: Text(
              outcome.error.isNotEmpty
                  ? outcome.error
                  : '${outcome.assertions.length} assertion(s) · '
                      '${outcome.caseId}'
                      '${outcome.runId.isEmpty ? "" : " · ${outcome.runId}"}',
            ),
            trailing: [
              Icon(
                expandedCase == outcome.caseId
                    ? Icons.expand_less
                    : Icons.expand_more,
                size: 20,
                color: CumulusColors.inkMuted,
              ),
            ],
          ),
          if (expandedCase == outcome.caseId) _CaseDetail(outcome: outcome),
        ],
      ],
    );
  }
}

class _CaseDetail extends StatelessWidget {
  final EvalCaseResult outcome;

  const _CaseDetail({required this.outcome});

  @override
  Widget build(BuildContext context) {
    final byCheckpoint = <String, List<EvalAssertion>>{};
    for (final assertion in outcome.assertions) {
      byCheckpoint.putIfAbsent(assertion.checkpoint, () => []).add(assertion);
    }
    return Container(
      color: CumulusColors.surfaceMuted.withValues(alpha: 0.55),
      padding: const EdgeInsets.fromLTRB(20, 14, 20, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (outcome.invariants.isNotEmpty) ...[
            const FieldLabel(
              'Ontology violations',
              hint: 'caught without anyone writing an assertion for them',
            ),
            for (final violation in outcome.invariants)
              Text(
                violation,
                style: CumulusText.meta.copyWith(color: CumulusColors.danger),
              ),
            const SizedBox(height: 12),
          ],
          for (final entry in byCheckpoint.entries) ...[
            Row(
              children: [
                StatusBadge(entry.key, dense: true, uppercase: true),
                const SizedBox(width: 8),
                Text(
                  '${entry.value.where((a) => a.passed).length}/'
                  '${entry.value.length} passed',
                  style: CumulusText.meta,
                ),
              ],
            ),
            const SizedBox(height: 6),
            for (final assertion in entry.value)
              Padding(
                padding: const EdgeInsets.only(bottom: 4, left: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(
                      assertion.passed ? Icons.check : Icons.close,
                      size: 13,
                      color: assertion.passed
                          ? CumulusTone.success.foreground
                          : CumulusTone.danger.foreground,
                    ),
                    const SizedBox(width: 8),
                    // Target and field are separate columns: joined into one
                    // truncated string, consecutive rows about the same object
                    // read as identical and the field — the part that
                    // distinguishes them — is what gets cut.
                    SizedBox(
                      width: 210,
                      child: Text(
                        assertion.target,
                        style: CumulusText.mono,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    SizedBox(
                      width: 150,
                      child: Text(
                        assertion.field.isEmpty ? '—' : assertion.field,
                        style: CumulusText.mono.copyWith(
                          color: CumulusColors.ink,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    SizedBox(
                      width: 96,
                      child: Text(assertion.rule, style: CumulusText.statCaption),
                    ),
                    Expanded(
                      child: Text(
                        assertion.passed
                            ? assertion.detail
                            : 'wanted ${assertion.expected.isEmpty ? "—" : assertion.expected}, '
                                'got ${assertion.actual.isEmpty ? "—" : assertion.actual}'
                                '${assertion.detail.isEmpty ? "" : " — ${assertion.detail}"}',
                        style: CumulusText.meta.copyWith(
                          color: assertion.passed
                              ? CumulusColors.inkMuted
                              : CumulusColors.danger,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 10),
          ],
          if (outcome.knowledgeRevisions.isNotEmpty)
            Text(
              'Read the corpus at: '
              '${outcome.knowledgeRevisions.entries.take(4).map((e) => "${e.key}@rev${e.value}").join(", ")}'
              '${outcome.knowledgeRevisions.length > 4 ? ", …" : ""}',
              style: CumulusText.statCaption,
            ),
        ],
      ),
    );
  }
}

class _CaseListCard extends StatelessWidget {
  final List<EvalCase> cases;
  final EvalRunResult? result;

  const _CaseListCard({required this.cases, required this.result});

  @override
  Widget build(BuildContext context) {
    if (cases.isEmpty) return const SizedBox.shrink();
    final byId = {
      for (final outcome in result?.cases ?? const <EvalCaseResult>[])
        outcome.caseId: outcome,
    };
    return ListCard(
      title: 'Cases',
      subtitle:
          'each one pins a behaviour that would otherwise regress silently · '
          'a fixture is expressed as the deliveries the real feeds would have '
          'made',
      children: [
        for (final testCase in cases)
          CumulusListRow(
            leading: Icon(
              byId[testCase.id] == null
                  ? Icons.radio_button_unchecked
                  : byId[testCase.id]!.passed
                      ? Icons.check_circle_outline
                      : Icons.error_outline,
              size: 18,
              color: byId[testCase.id] == null
                  ? CumulusColors.inkMuted
                  : byId[testCase.id]!.passed
                      ? CumulusTone.success.foreground
                      : CumulusTone.danger.foreground,
            ),
            title: Text(testCase.name),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (testCase.summary.isNotEmpty) Text(testCase.summary),
                const SizedBox(height: 4),
                Text(
                  [
                    testCase.id,
                    if (testCase.deliveries > 0)
                      '${testCase.deliveries} feed delivery(s)',
                    if (testCase.fixtureObjects > 0)
                      '${testCase.fixtureObjects} fixture object(s)',
                    if (testCase.decisions > 0)
                      '${testCase.decisions} scripted decision(s)',
                    'checkpoints: ${testCase.checkpoints.join(", ")}',
                  ].join('  ·  '),
                  style: CumulusText.mono,
                ),
              ],
            ),
          ),
      ],
    );
  }
}
