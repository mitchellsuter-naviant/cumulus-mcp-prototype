import 'package:flutter/material.dart';

import '../models/eval.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'side_drawer.dart';
import 'ui_kit.dart';

/// A flow's evaluation, answering three questions in the order they get asked.
///
/// 1. **What does this flow promise?** The graded surface, and what it ignores
///    on purpose — a due date is the vendor's payment terms, so grading it
///    would be grading the wrong system. `ignore` is a decision, so it is
///    shown rather than hidden.
/// 2. **Does it still keep that promise?** One run button, pass or fail per
///    case, one click into a failure.
/// 3. **How do I add a case?** The authoring path that exists, said once.
///
/// There is exactly one population of cases here, and the screen never counts
/// it twice: the declared cases and the graded cases are the same rows, and a
/// result annotates them rather than repeating them under a second noun.
///
/// The per-field rules and the milestone declarations are the spec, not the
/// result, and they live one control away in [_openSpecDrawer]. Reading "what
/// does AP promise about an Invoice" stays possible; it stops being the first
/// thing on screen.
class EvalPanel extends StatefulWidget {
  final ApiClient api;
  final String flowId;

  const EvalPanel({super.key, required this.api, required this.flowId});

  @override
  State<EvalPanel> createState() => _EvalPanelState();
}

class _EvalPanelState extends State<EvalPanel> {
  EvalSpecSummary? _summary;
  EvalSpec? _spec;
  List<EvalCase> _cases = const [];
  EvalRunResult? _result;
  String? _error;
  bool _loading = true;
  bool _running = false;
  String? _openCase;

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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        _promiseCard(spec, summary),
        if (_error != null) ErrorNotice(message: _error!),
        _casesCard(spec),
      ],
    );
  }

  // ---- 1. what does this flow promise? ------------------------------------

  Widget _promiseCard(EvalSpec spec, EvalSpecSummary summary) {
    final ignored = _ignoredFields(spec);
    return CumulusCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            title: spec.id,
            subtitle:
                'v${spec.version} · what this flow is answerable for, graded '
                'in a world of its own',
            trailing: OutlinedButton.icon(
              onPressed: () => _openSpecDrawer(context, spec),
              icon: const Icon(Icons.gavel_outlined, size: 15),
              label: const Text('How this flow is judged'),
            ),
          ),
          const SizedBox(height: 12),
          Prose(spec.summary),
          const SizedBox(height: 16),
          Text(
            '${summary.gradedFields} field'
            '${summary.gradedFields == 1 ? '' : 's'} and '
            '${summary.gradedLinks} edge${summary.gradedLinks == 1 ? '' : 's'} '
            'are graded, at ${summary.milestones} point'
            '${summary.milestones == 1 ? '' : 's'} in the run.',
            style: CumulusText.body.copyWith(color: CumulusColors.ink),
          ),
          if (ignored.isNotEmpty) ...[
            const SizedBox(height: 14),
            const FieldLabel(
              'Ignored on purpose',
              hint:
                  'these belong to another system — grading them would grade it',
            ),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                for (final entry in ignored)
                  Tooltip(
                    message: entry.$2.isEmpty
                        ? 'Not something this flow establishes.'
                        : plainProse(entry.$2),
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 5,
                      ),
                      decoration: BoxDecoration(
                        color: CumulusColors.canvas,
                        borderRadius: BorderRadius.circular(7),
                        border: Border.all(color: CumulusColors.borderStrong),
                      ),
                      child: Text(
                        entry.$1,
                        style: CumulusText.mono.copyWith(
                          color: CumulusColors.inkMuted,
                          decoration: TextDecoration.lineThrough,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  /// Every ignored field the spec declares, as `Type.field` with its note.
  /// Deduped: the same rule restated at two milestones is one decision.
  List<(String, String)> _ignoredFields(EvalSpec spec) {
    final seen = <String, String>{};
    for (final milestone in spec.milestones) {
      for (final rule in milestone.objects) {
        for (final field in rule.fields.where((f) => f.ignored)) {
          seen.putIfAbsent('${rule.type}.${field.field}', () => field.note);
        }
      }
    }
    return [for (final e in seen.entries) (e.key, e.value)];
  }

  // ---- 2 and 3. does it keep it, and how do I add one? --------------------

  Widget _casesCard(EvalSpec spec) {
    final result = _result;
    final byId = {
      for (final outcome in result?.cases ?? const <EvalCaseResult>[])
        outcome.caseId: outcome,
    };
    final milestones = {for (final m in spec.milestones) m.id: m};
    return ListCard(
      title: 'Cases · ${_cases.length}',
      subtitle: 'each pins one behaviour that would otherwise regress silently',
      headerTrailing: FilledButton.icon(
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
              : 'Run ${_cases.length} case${_cases.length == 1 ? '' : 's'}',
        ),
      ),
      children: [
        if (result != null)
          _ResultBanner(
            result: result,
            declared: _cases.length,
            // Opens rather than toggles: a regressed chip is a request to
            // look at that case, never to put it away.
            onOpenCase: (id) => setState(() => _openCase = id),
          )
        else
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 14),
            child: Text(
              'No result yet. Results are not persisted — a pass rate only '
              'means something against the knowledge revisions it read — so '
              'running the cases here is what produces one.',
              style: CumulusText.meta,
            ),
          ),
        for (final testCase in _cases) ...[
          _caseRow(testCase, byId[testCase.id]),
          if (_openCase == testCase.id && byId[testCase.id] != null)
            _CaseChecks(outcome: byId[testCase.id]!, milestones: milestones),
        ],
        _authoringFooter(),
      ],
    );
  }

  void _open(String caseId) =>
      setState(() => _openCase = _openCase == caseId ? null : caseId);

  Widget _caseRow(EvalCase testCase, EvalCaseResult? outcome) {
    final open = _openCase == testCase.id;
    return CumulusListRow(
      onTap: outcome == null ? null : () => _open(testCase.id),
      leading: Icon(
        outcome == null
            ? Icons.radio_button_unchecked
            : outcome.passed
            ? Icons.check_circle
            : Icons.cancel,
        size: 19,
        color: outcome == null
            ? CumulusColors.inkMuted
            : outcome.passed
            ? CumulusTone.success.foreground
            : CumulusTone.danger.foreground,
      ),
      // Case prose is authored in Python beside a Markdown export, so it
      // arrives with its markers on.
      title: Prose(testCase.name, style: CumulusText.rowTitle),
      subtitle: Prose(
        testCase.summary.isEmpty ? testCase.id : testCase.summary,
        style: CumulusText.meta,
      ),
      trailing: [
        if (outcome != null) ...[
          if (outcome.failures.isNotEmpty)
            StatusBadge(
              '${outcome.failures.length} failed',
              tone: CumulusTone.danger,
              dense: true,
            )
          else
            Text(
              '${outcome.assertions.length} checks',
              style: CumulusText.meta,
            ),
          if (outcome.invariants.isNotEmpty) ...[
            const SizedBox(width: 6),
            StatusBadge(
              '${outcome.invariants.length} ontology',
              tone: CumulusTone.danger,
              dense: true,
            ),
          ],
          const SizedBox(width: 8),
          Icon(
            open ? Icons.expand_less : Icons.expand_more,
            size: 20,
            color: CumulusColors.inkMuted,
          ),
        ],
      ],
    );
  }

  Widget _authoringFooter() {
    final worlds = {
      for (final c in _cases)
        if (c.fixtureId.isNotEmpty) c.fixtureId,
    };
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 14, 20, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const FieldLabel('Adding a case'),
          Prose(
            'Cases are declared in `server/app/eval_specs.py` and exported to '
            '`knowledge:${_summary?.assetId ?? 'flow-eval-specs'}`. A case '
            'names the world it runs in rather than carrying one, so these '
            '${_cases.length} cases share ${worlds.length} '
            'world${worlds.length == 1 ? '' : 's'} — re-author a world and '
            'every case naming it comes along.',
            style: CumulusText.meta,
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The result
// ---------------------------------------------------------------------------

class _ResultBanner extends StatelessWidget {
  final EvalRunResult result;

  /// How many cases the spec declares. A run graded all of them unless
  /// somebody asked the API for a subset, and then the difference is worth
  /// saying rather than leaving two counts to disagree on screen.
  final int declared;
  final ValueChanged<String> onOpenCase;

  const _ResultBanner({
    required this.result,
    required this.declared,
    required this.onOpenCase,
  });

  @override
  Widget build(BuildContext context) {
    final total = result.passed + result.failed;
    final ok = result.failed == 0 && result.invariantViolations == 0;
    final tone = ok ? CumulusTone.success : CumulusTone.danger;
    final gate = result.gate;
    return Container(
      color: tone.background.withValues(alpha: 0.5),
      padding: const EdgeInsets.fromLTRB(20, 14, 20, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                ok ? Icons.check_circle : Icons.cancel,
                size: 18,
                color: tone.foreground,
              ),
              const SizedBox(width: 10),
              Text(
                '${result.passed} of $total passed',
                style: CumulusText.rowTitle.copyWith(color: tone.foreground),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  '${result.assertions - result.failedAssertions}'
                  '/${result.assertions} checks'
                  '${total == declared ? '' : ' · ${declared - total} of the $declared cases were not graded'}'
                  '${result.invariantViolations == 0 ? '' : ' · ${result.invariantViolations} ontology violations'}'
                  ' · ${agoIso(result.atIso)}'
                  '${result.launchedBy.isEmpty ? '' : ' by ${result.launchedBy}'}',
                  style: CumulusText.meta,
                ),
              ),
              if (gate != null)
                StatusBadge(
                  _gateLabel(gate),
                  tone: _gateTone(gate),
                  dense: true,
                  uppercase: true,
                ),
            ],
          ),
          // The gate expands only when it blocks. A promotable one has
          // nothing to say that the badge has not already said.
          if (gate != null && !gate.passed) ...[
            const SizedBox(height: 10),
            _GateDetail(gate: gate, result: result, onOpenCase: onOpenCase),
          ],
        ],
      ),
    );
  }
}

String _gateLabel(EvalGate gate) => !gate.comparable
    ? 'comparison refused'
    : gate.passed
    ? 'promotable'
    : 'blocked';

CumulusTone _gateTone(EvalGate gate) => !gate.comparable
    ? CumulusTone.warn
    : gate.passed
    ? CumulusTone.success
    : CumulusTone.danger;

/// Why the gate blocked — and, when it refused, the fact that refusing is not
/// the same as failing.
class _GateDetail extends StatelessWidget {
  final EvalGate gate;
  final EvalRunResult result;
  final ValueChanged<String> onOpenCase;

  const _GateDetail({
    required this.gate,
    required this.result,
    required this.onOpenCase,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(
          color: _gateTone(gate).foreground.withValues(alpha: 0.35),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!gate.comparable)
            Prose(
              'The comparison was refused, not failed. This run and the one at '
              '${gate.baselineAtIso} measured different things — the spec or '
              'the set of cases changed — so their pass rates answer different '
              'questions. Nothing here says the flow regressed. Re-run the '
              'baseline to get a comparison back.'
              '${result.measured.isEmpty ? '' : ' This run measured `${result.measured}`.'}',
              style: CumulusText.body,
            )
          else
            for (final reason in gate.reasons)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Prose(reason, style: CumulusText.body),
              ),
          if (gate.regressed.isNotEmpty) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                for (final caseId in gate.regressed)
                  ActionChip(
                    label: Text(caseId, style: CumulusText.mono),
                    onPressed: () => onOpenCase(caseId),
                    visualDensity: VisualDensity.compact,
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// One case's checks, grouped by the milestone they were evaluated at.
///
/// The milestone is the spine: a check means "at this point in the run, this
/// was true", and reading it without knowing which point is reading half a
/// sentence. Each group leads with where that point landed — in the words the
/// spec publishes, never a step id — because a failure to reach it makes
/// every other check under it moot.
class _CaseChecks extends StatelessWidget {
  final EvalCaseResult outcome;
  final Map<String, EvalMilestone> milestones;

  const _CaseChecks({required this.outcome, required this.milestones});

  @override
  Widget build(BuildContext context) {
    final groups = <String, List<EvalAssertion>>{};
    for (final assertion in outcome.assertions) {
      groups.putIfAbsent(assertion.milestone, () => []).add(assertion);
    }
    return Container(
      color: CumulusColors.surfaceMuted.withValues(alpha: 0.55),
      padding: const EdgeInsets.fromLTRB(20, 14, 20, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 14,
        children: [
          if (outcome.error.isNotEmpty)
            Text(
              outcome.error,
              style: CumulusText.body.copyWith(color: CumulusColors.danger),
            ),
          if (outcome.invariants.isNotEmpty)
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const FieldLabel(
                  'Ontology violations',
                  hint: 'caught without anyone writing a check for them',
                ),
                for (final violation in outcome.invariants)
                  Text(
                    violation,
                    style: CumulusText.meta.copyWith(
                      color: CumulusColors.danger,
                    ),
                  ),
              ],
            ),
          for (final entry in groups.entries)
            _MilestoneChecks(
              id: entry.key,
              milestone: milestones[entry.key],
              checks: entry.value,
            ),
        ],
      ),
    );
  }
}

class _MilestoneChecks extends StatelessWidget {
  final String id;
  final EvalMilestone? milestone;
  final List<EvalAssertion> checks;

  const _MilestoneChecks({
    required this.id,
    required this.milestone,
    required this.checks,
  });

  @override
  Widget build(BuildContext context) {
    final reached = checks
        .where((c) => c.target == 'milestone' && c.rule == 'reached')
        .firstOrNull;
    final path = checks.where((c) => c.target == 'trajectory').toList();
    // The world checks, gathered under the object each one is about. Fourteen
    // rows repeating one forty-character natural key say the key fourteen
    // times and the thing that distinguishes them once.
    final world = <String, List<EvalAssertion>>{};
    for (final check in checks) {
      if (check.target == 'milestone' || check.target == 'trajectory') continue;
      world.putIfAbsent(_subject(check), () => []).add(check);
    }
    final failed = checks.where((c) => !c.passed).length;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            StatusBadge(
              id,
              dense: true,
              uppercase: true,
              tone: failed == 0 ? CumulusTone.neutral : CumulusTone.danger,
            ),
            const SizedBox(width: 9),
            Expanded(
              child: Prose(
                milestone?.anchorLabel ??
                    'a point this spec no longer declares',
                style: CumulusText.meta,
              ),
            ),
            Text(
              '${checks.length - failed}/${checks.length}',
              style: CumulusText.meta,
            ),
          ],
        ),
        // Reaching the milestone is itself a claim, so it gets a line of its
        // own rather than sitting in the list as one row among forty.
        if (reached != null) _reachedLine(reached),
        for (final entry in world.entries) ...[
          const SizedBox(height: 9),
          Text(
            entry.key,
            style: CumulusText.mono.copyWith(color: CumulusColors.inkSecondary),
          ),
          const SizedBox(height: 3),
          for (final check in entry.value) _CheckRow(check: check),
        ],
        if (path.isNotEmpty) ...[
          const SizedBox(height: 10),
          const FieldLabel('Path', hint: 'how the run got here'),
          for (final check in path) _CheckRow(check: check, path: true),
        ],
      ],
    );
  }

  Widget _reachedLine(EvalAssertion reached) {
    if (reached.passed) {
      return Padding(
        padding: const EdgeInsets.only(left: 4, top: 4),
        child: Text(
          'reached at ${reached.actual}',
          style: CumulusText.statCaption,
        ),
      );
    }
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: CumulusTone.danger.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Prose(
            'Never reached. The case asks for ${reached.expected}.',
            style: CumulusText.body.copyWith(color: CumulusColors.danger),
          ),
          if (reached.detail.isNotEmpty) ...[
            const SizedBox(height: 4),
            Prose(reached.detail, style: CumulusText.body),
          ],
          if (reached.actual.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              'The run offered: ${reached.actual}',
              style: CumulusText.mono.copyWith(
                color: CumulusColors.inkSecondary,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// The object a check is about. An edge check names it as
/// `Invoice[key] --issued-->`; the edge belongs with the check, not the
/// heading, or one object would head three groups.
String _subject(EvalAssertion check) {
  final arrow = check.target.indexOf(' --');
  return arrow < 0 ? check.target : check.target.substring(0, arrow);
}

/// What this check is about, under its object: a field name, an edge, or the
/// rule's own name when neither applies.
String _checkLabel(EvalAssertion check) {
  final arrow = check.target.indexOf(' --');
  final edge = arrow < 0 ? '' : check.target.substring(arrow + 1);
  final name = check.field.isEmpty ? check.rule : check.field;
  return edge.isEmpty ? name : '$edge $name';
}

class _CheckRow extends StatelessWidget {
  final EvalAssertion check;

  /// A path check states its claim as a sentence; there is no object to column.
  final bool path;

  const _CheckRow({required this.check, this.path = false});

  @override
  Widget build(BuildContext context) {
    final message = check.passed
        ? check.detail
        : 'wanted ${check.expected.isEmpty ? '—' : check.expected}, '
              'got ${check.actual.isEmpty ? '—' : check.actual}'
              '${check.detail.isEmpty ? '' : ' — ${check.detail}'}';
    return Padding(
      padding: EdgeInsets.only(bottom: 3, left: path ? 4 : 14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            check.passed ? Icons.check : Icons.close,
            size: 13,
            color: check.passed
                ? CumulusTone.success.foreground
                : CumulusTone.danger.foreground,
          ),
          const SizedBox(width: 8),
          Expanded(
            // A path claim is a sentence and needs the room; a field name is
            // a word, and holding a word in a wide column pushes the reason
            // it failed off to the right.
            flex: path ? 5 : 3,
            child: path
                ? Prose(
                    trajectoryPhrase(check.field, check.expected),
                    style: CumulusText.meta.copyWith(color: CumulusColors.ink),
                  )
                : Text(
                    _checkLabel(check),
                    style: CumulusText.mono.copyWith(color: CumulusColors.ink),
                  ),
          ),
          const SizedBox(width: 16),
          Expanded(
            flex: 7,
            // A trajectory check carries its spec note as the detail, and a
            // milestone's failure detail is written with refs in backticks.
            child: Prose(
              message,
              style: CumulusText.meta.copyWith(
                color: check.passed
                    ? CumulusColors.inkMuted
                    : CumulusColors.danger,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The spec, one control away
// ---------------------------------------------------------------------------

void _openSpecDrawer(BuildContext context, EvalSpec spec) {
  showSideDrawer<void>(
    context: context,
    title: 'How this flow is judged',
    subtitle: '${spec.id} · v${spec.version}',
    width: 720,
    builder: (context) => Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Prose(
          'The spec is an overlay on the enterprise model: the ontology says '
          'what an Invoice is, and this says which parts of it the flow is '
          'answerable for, how tightly to compare them, and at which point in '
          'a run to look. Cases supply only the values.',
        ),
        if (spec.ignoreFields.isNotEmpty) ...[
          const SizedBox(height: 10),
          // Once, for the whole spec. It used to be restated under every
          // milestone, which made four milestones look like four decisions.
          Text(
            'Never graded on any type: ${spec.ignoreFields.join(", ")}.',
            style: CumulusText.statCaption,
          ),
        ],
        for (final milestone in spec.milestones) ...[
          const SizedBox(height: 22),
          _MilestoneSpec(milestone: milestone),
        ],
      ],
    ),
  );
}

class _MilestoneSpec extends StatelessWidget {
  final EvalMilestone milestone;

  const _MilestoneSpec({required this.milestone});

  @override
  Widget build(BuildContext context) {
    final isFinal = milestone.anchor == 'terminal';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            StatusBadge(
              milestone.id,
              tone: isFinal ? CumulusTone.neutral : CumulusTone.accent,
              dense: true,
              uppercase: true,
            ),
            const SizedBox(width: 9),
            // The anchor names what the run *does*, never a step id: that is
            // what lets a ModelCall be split or renamed without a case
            // noticing.
            Expanded(
              child: Prose(milestone.anchorLabel, style: CumulusText.meta),
            ),
          ],
        ),
        if (milestone.label.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text(milestone.label, style: CumulusText.rowTitle),
        ],
        if (milestone.note.isNotEmpty) ...[
          const SizedBox(height: 6),
          Prose(milestone.note),
        ],
        for (final rule in milestone.objects) ...[
          const SizedBox(height: 14),
          Row(
            children: [
              StatusBadge(rule.type, tone: CumulusTone.teal, dense: true),
              const SizedBox(width: 8),
              if (rule.fields.isEmpty && rule.links.isEmpty)
                Expanded(
                  child: Text(
                    'no field rules — declared so a case can assert the '
                    'object is absent here',
                    style: CumulusText.meta,
                  ),
                ),
              if (rule.unlistedFields == 'require') ...[
                const Tooltip(
                  message:
                      'Any field the flow claims that this spec does not name '
                      'is reported as drift.',
                  child: StatusBadge(
                    'no drift',
                    tone: CumulusTone.warn,
                    dense: true,
                  ),
                ),
              ],
            ],
          ),
          if (rule.fields.isNotEmpty || rule.links.isNotEmpty) ...[
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
        ],
        if (milestone.trajectory.isNotEmpty) ...[
          const SizedBox(height: 14),
          const FieldLabel('Path', hint: 'claims about how the run gets here'),
          for (final rule in milestone.trajectory)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Prose(
                rule.note.isEmpty
                    ? trajectoryPhrase(rule.kind, rule.expected)
                    : '${trajectoryPhrase(rule.kind, rule.expected)} — '
                          '${rule.note}',
                style: CumulusText.meta,
              ),
            ),
        ],
      ],
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
          : plainProse(rule.note),
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
          : plainProse(rule.note),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(7),
          border: Border.all(
            color: CumulusColors.accent.withValues(alpha: 0.5),
          ),
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
// Words
// ---------------------------------------------------------------------------

/// A trajectory rule as a claim rather than a token pair.
///
/// The chip used to print `kind` and `expected` side by side, which produced
/// `read document:` — a verb, a prefix, and a colon left dangling in the air.
/// The prefix is correct and load-bearing; what was missing was the sentence
/// around it.
String trajectoryPhrase(String kind, String expected) {
  // A spec declares the rule and leaves the value to the case, so half the
  // time there is nothing to quote. Saying so beats quoting the empty string.
  if (expected.isEmpty) {
    return switch (kind) {
      'step_ran' => 'the step the case names ran',
      'step_absent' => 'the step the case names did not run',
      'gate_raised' => 'the gate the case names was raised',
      'gate_count' => 'as many gates as the case says',
      'terminal_status' => 'the run ended as the case says',
      'queue' => 'a task landed in the queue the case names',
      'model_calls_at_most' => 'no more model calls than the case allows',
      'wrote' => 'the case names what must have been written',
      'read' => 'the case names what must have been read',
      _ => kind,
    };
  }
  final it = '`$expected`';
  return switch (kind) {
    'step_ran' => 'the step $it ran',
    'step_absent' => 'the step $it did not run',
    'gate_raised' => 'the gate $it was raised',
    'gate_count' =>
      'exactly $expected gate${expected == '1' ? ' was' : 's were'} raised',
    'terminal_status' => 'the run ended $it',
    'queue' => 'a task landed in the $it queue',
    'model_calls_at_most' =>
      'at most $expected model call${expected == '1' ? '' : 's'}',
    'wrote' => 'something under $it was written',
    'read' => 'something under $it was read',
    _ => '$kind $expected',
  };
}

/// Inline markdown, rendered rather than printed.
///
/// The specs are authored in Python and read by two consumers — a Markdown
/// export and this screen — so `code`, *emphasis* and **strong** are
/// load-bearing at the source and pure punctuation here. Rendering them costs
/// one small parser; stripping them at the source would cost the export its
/// formatting.
class Prose extends StatelessWidget {
  final String text;
  final TextStyle style;

  const Prose(this.text, {super.key, this.style = CumulusText.body});

  @override
  Widget build(BuildContext context) =>
      Text.rich(TextSpan(children: proseSpans(text, style)), style: style);
}

final _markers = RegExp(r'`([^`]+)`|\*\*([^*]+)\*\*|\*([^*\n]+)\*');

/// The same prose where only a plain string will do — a tooltip.
String plainProse(String text) =>
    text.replaceAllMapped(_markers, (m) => m[1] ?? m[2] ?? m[3] ?? '');

List<InlineSpan> proseSpans(String text, TextStyle base) {
  final spans = <InlineSpan>[];
  var index = 0;
  for (final match in _markers.allMatches(text)) {
    if (match.start > index) {
      spans.add(TextSpan(text: text.substring(index, match.start)));
    }
    if (match[1] != null) {
      spans.add(
        TextSpan(
          text: match[1],
          style: base.copyWith(
            fontFamily: CumulusText.mono.fontFamily,
            fontFamilyFallback: CumulusText.mono.fontFamilyFallback,
            fontSize: (base.fontSize ?? 13) - 0.5,
            color: CumulusColors.ink,
          ),
        ),
      );
    } else if (match[2] != null) {
      spans.add(
        TextSpan(
          text: match[2],
          style: const TextStyle(
            fontWeight: FontWeight.w600,
            color: CumulusColors.ink,
          ),
        ),
      );
    } else {
      spans.add(
        TextSpan(
          text: match[3],
          style: const TextStyle(fontStyle: FontStyle.italic),
        ),
      );
    }
    index = match.end;
  }
  if (index < text.length) spans.add(TextSpan(text: text.substring(index)));
  return spans;
}
