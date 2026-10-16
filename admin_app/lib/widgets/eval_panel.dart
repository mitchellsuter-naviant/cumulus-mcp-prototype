import 'package:flutter/material.dart';

import '../models/eval.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'side_drawer.dart';
import 'skeleton.dart';
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

/// Column widths for the test matrix. One place, so the head and its rows
/// cannot drift apart.
const _evalResultW = 92.0;
const _evalWhenW = 100.0;
const _evalActionsW = 84.0;
const _evalGap = 14.0;

/// Below this the expected-behaviour column is dropped: the case name and its
/// verdict are what the table is for, and squeezing three text columns into a
/// narrow card gives you three ellipses.
const _evalWideWidth = 900.0;

class _EvalPanelState extends State<EvalPanel> {
  EvalSpecSummary? _summary;
  EvalSpec? _spec;
  List<EvalCase> _cases = const [];

  /// The last full run — the only thing that can carry a gate. A gate is a
  /// comparison of a whole suite against a baseline, and re-running one case
  /// does not produce one.
  EvalRunResult? _result;

  /// The current verdict per case, and when it was reached. Held beside
  /// [_result] rather than read out of it because a single case can be re-run
  /// on its own, and when it is, every other row has to keep the verdict it
  /// already has instead of blanking.
  final Map<String, EvalCaseResult> _outcomes = {};
  final Map<String, String> _gradedAtIso = {};

  String? _error;
  bool _loading = true;
  bool _running = false;
  String? _runningCase;
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
      // Once the spec id is known the three reads behind it are independent of
      // one another, so they go out together and the panel waits for the
      // slowest rather than for their sum. Grouped by `Future.wait` rather
      // than awaited in a row: a first failure would otherwise escape while
      // the other two are still in flight, and their errors would land with
      // nobody listening.
      final specId = specs.first.id;
      final Future<EvalSpec> specRead = widget.api.getEvalSpec(specId);
      final Future<List<EvalCase>> casesRead = widget.api.listEvalCases(specId);
      final Future<EvalRunResult?> lastRead = widget.api.lastEvalResult(specId);
      await Future.wait<void>([specRead, casesRead, lastRead]);
      final spec = await specRead;
      final cases = await casesRead;
      final last = await lastRead;
      if (!mounted) return;
      setState(() {
        _summary = specs.first;
        _spec = spec;
        _cases = cases;
        _result = last;
        if (last != null) _absorb(last, full: true);
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

  /// Fold a result into the per-case verdicts. A full run replaces them; a
  /// single case updates its own row and leaves the others standing.
  void _absorb(EvalRunResult result, {required bool full}) {
    if (full) {
      _outcomes.clear();
      _gradedAtIso.clear();
    }
    for (final outcome in result.cases) {
      _outcomes[outcome.caseId] = outcome;
      _gradedAtIso[outcome.caseId] = result.atIso;
    }
  }

  /// Run the suite, or one case of it. The same call either way — the API
  /// takes the case ids it should grade, and an empty list means all of them.
  Future<void> _launch({EvalCase? single}) async {
    final summary = _summary;
    if (summary == null) return;
    setState(() {
      if (single == null) {
        _running = true;
      } else {
        _runningCase = single.id;
      }
      _error = null;
    });
    try {
      final result = await widget.api.runEval(
        summary.id,
        caseIds: single == null ? const [] : [single.id],
      );
      if (!mounted) return;
      setState(() {
        if (single == null) _result = result;
        _absorb(result, full: single == null);
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.statusCode == 403
            ? 'Launching an eval needs admin. (A CI key may do it — that is '
                  'the one write here a credential should make.)'
            : e.message;
      });
    } finally {
      if (mounted) {
        setState(() {
          _running = false;
          _runningCase = null;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      // The panel is a header bar over a test matrix; while the spec is in
      // flight it holds that shape rather than collapsing to a spinner.
      return const SkeletonPulse(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          spacing: 16,
          children: [
            SkeletonCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SkeletonBar(width: 210, height: 14),
                  SizedBox(height: 10),
                  SkeletonBar(width: 320, height: 10),
                  SizedBox(height: 18),
                  Row(
                    spacing: 10,
                    children: [
                      SkeletonPill(width: 88),
                      SkeletonPill(width: 104),
                      SkeletonPill(width: 72),
                    ],
                  ),
                ],
              ),
            ),
            SkeletonCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SkeletonBar(width: 148, height: 10),
                  SizedBox(height: 10),
                  SkeletonTableRows(rows: 6, columnFlex: [4, 3, 2, 2]),
                ],
              ),
            ),
          ],
        ),
      );
    }
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
        _headerBar(spec, summary),
        if (_error != null) ErrorNotice(message: _error!),
        _matrixCard(spec),
      ],
    );
  }

  // ---- the header bar -----------------------------------------------------

  /// What is being graded, how much of it passes, and the two controls.
  ///
  /// This replaced a card that spent four paragraphs and a chip cloud saying
  /// what the spec *is* — which is a thing to read once, in the drawer, not a
  /// thing to scroll past every time somebody checks a pass rate.
  Widget _headerBar(EvalSpec spec, EvalSpecSummary summary) {
    final passed = _outcomes.values.where((o) => o.passed).length;
    final failed = _outcomes.length - passed;
    final gate = _result?.gate;
    final busy = _running || _runningCase != null;
    return CumulusCard(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Wrap(
                  crossAxisAlignment: WrapCrossAlignment.center,
                  spacing: 10,
                  runSpacing: 6,
                  children: [
                    Text(
                      spec.id,
                      style: CumulusText.mono.copyWith(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    StatusBadge('v${spec.version}', dense: true),
                    if (gate != null)
                      StatusBadge(
                        _gateLabel(gate),
                        tone: _gateTone(gate),
                        dense: true,
                        uppercase: true,
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              OutlinedButton.icon(
                onPressed: () => _openSpecDrawer(context, spec),
                icon: const Icon(Icons.gavel_outlined, size: 15),
                label: const Text('How this flow is judged'),
              ),
              const SizedBox(width: 10),
              FilledButton.icon(
                onPressed: busy ? null : () => _launch(),
                icon: _running
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.play_arrow, size: 16),
                label: Text(_running ? 'Running…' : 'Run all evals'),
              ),
            ],
          ),
          const SizedBox(height: 16),
          _PassRateBar(passed: passed, failed: failed, total: _cases.length),
          const SizedBox(height: 9),
          Text(
            _summaryLine(summary, passed, failed),
            style: CumulusText.meta,
          ),
          // The gate expands only when it blocks. A promotable one has
          // nothing to say that the badge has not already said.
          if (gate != null && !gate.passed) ...[
            const SizedBox(height: 12),
            _GateDetail(
              gate: gate,
              result: _result!,
              onOpenCase: (id) => setState(() => _openCase = id),
            ),
          ],
        ],
      ),
    );
  }

  String _summaryLine(EvalSpecSummary summary, int passed, int failed) {
    final total = _cases.length;
    final graded = passed + failed;
    if (graded == 0) {
      return '$total case${total == 1 ? '' : 's'} declared, none graded yet · '
          '${summary.gradedFields} field'
          '${summary.gradedFields == 1 ? '' : 's'} and ${summary.gradedLinks} '
          'edge${summary.gradedLinks == 1 ? '' : 's'} are graded at '
          '${summary.milestones} point'
          '${summary.milestones == 1 ? '' : 's'} in the run';
    }
    final result = _result;
    return '$passed of $graded graded case${graded == 1 ? '' : 's'} passing'
        '${graded == total ? '' : ' · ${total - graded} not yet graded'}'
        '${result == null ? '' : ' · ${result.assertions - result.failedAssertions}/${result.assertions} checks'}'
        '${result == null || result.invariantViolations == 0 ? '' : ' · ${result.invariantViolations} ontology violations'}'
        '${result == null ? '' : ' · last full run ${agoIso(result.atIso)}'}'
        '${result == null || result.launchedBy.isEmpty ? '' : ' by ${result.launchedBy}'}';
  }

  // ---- the test matrix ----------------------------------------------------

  void _open(String caseId) =>
      setState(() => _openCase = _openCase == caseId ? null : caseId);

  Widget _matrixCard(EvalSpec spec) {
    final milestones = {for (final m in spec.milestones) m.id: m};
    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= _evalWideWidth;
        return ListCard(
          title: 'Test cases · ${_cases.length}',
          subtitle:
              'each pins one behaviour that would otherwise regress silently',
          children: [
            _MatrixHead(wide: wide),
            for (final testCase in _cases) ...[
              _MatrixRow(
                testCase: testCase,
                outcome: _outcomes[testCase.id],
                gradedAtIso: _gradedAtIso[testCase.id] ?? '',
                open: _openCase == testCase.id,
                running: _runningCase == testCase.id,
                busy: _running || _runningCase != null,
                wide: wide,
                onOpen: () => _open(testCase.id),
                onRun: () => _launch(single: testCase),
              ),
              if (_openCase == testCase.id && _outcomes[testCase.id] != null)
                _CaseChecks(
                  outcome: _outcomes[testCase.id]!,
                  milestones: milestones,
                ),
            ],
            _authoringFooter(),
          ],
        );
      },
    );
  }

  /// Where the cases come from and what they run against, in one line. It
  /// used to be a paragraph; nothing in it was worth four.
  Widget _authoringFooter() {
    final worlds = {
      for (final c in _cases)
        if (c.fixtureId.isNotEmpty) c.fixtureId,
    };
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 4),
      child: Text(
        'Declared in server/app/eval_specs.py, exported to '
        'knowledge:${_summary?.assetId ?? 'flow-eval-specs'} · '
        '${_cases.length} case${_cases.length == 1 ? '' : 's'} over '
        '${worlds.length} shared world${worlds.length == 1 ? '' : 's'} · '
        'results are not persisted — a pass rate only means something against '
        'the knowledge revisions it read, so it is only ever the one you just '
        'ran',
        style: CumulusText.meta,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Matrix head, rows and the pass-rate bar
// ---------------------------------------------------------------------------

/// Pass, fail and not-yet-graded as one bar. Three segments rather than a
/// percentage, because "8 of 14" and "8 of 8, six ungraded" are different
/// states that round to very different confidence.
class _PassRateBar extends StatelessWidget {
  final int passed;
  final int failed;
  final int total;

  const _PassRateBar({
    required this.passed,
    required this.failed,
    required this.total,
  });

  @override
  Widget build(BuildContext context) {
    final ungraded = (total - passed - failed).clamp(0, total);
    return ClipRRect(
      borderRadius: BorderRadius.circular(CumulusRadius.pill),
      child: SizedBox(
        height: 7,
        child: (passed + failed + ungraded) == 0
            ? const ColoredBox(color: CumulusColors.border)
            : Row(
                children: [
                  if (passed > 0)
                    Expanded(
                      flex: passed,
                      child: const ColoredBox(color: CumulusColors.success),
                    ),
                  if (failed > 0)
                    Expanded(
                      flex: failed,
                      child: const ColoredBox(color: CumulusColors.danger),
                    ),
                  if (ungraded > 0)
                    Expanded(
                      flex: ungraded,
                      child: const ColoredBox(color: CumulusColors.border),
                    ),
                ],
              ),
      ),
    );
  }
}

class _MatrixHead extends StatelessWidget {
  final bool wide;

  const _MatrixHead({required this.wide});

  @override
  Widget build(BuildContext context) {
    Widget cell(String text, double width, {TextAlign align = TextAlign.left}) =>
        SizedBox(
          width: width,
          child: Text(
            text.toUpperCase(),
            textAlign: align,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: CumulusText.statLabel,
          ),
        );
    return Container(
      color: CumulusColors.surfaceMuted.withValues(alpha: 0.45),
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      child: Row(
        children: [
          cell('Result', _evalResultW),
          const SizedBox(width: _evalGap),
          Expanded(
            flex: 5,
            child: Text('TEST CASE', style: CumulusText.statLabel),
          ),
          if (wide) ...[
            const SizedBox(width: _evalGap),
            Expanded(
              flex: 6,
              child: Text('EXPECTED BEHAVIOUR', style: CumulusText.statLabel),
            ),
          ],
          const SizedBox(width: _evalGap),
          cell('Last eval', _evalWhenW),
          const SizedBox(width: _evalGap),
          cell('Actions', _evalActionsW, align: TextAlign.right),
        ],
      ),
    );
  }
}

/// One case: its verdict, what it pins, what it expects, when that was last
/// established, and the button that establishes it again.
class _MatrixRow extends StatefulWidget {
  final EvalCase testCase;
  final EvalCaseResult? outcome;
  final String gradedAtIso;
  final bool open;
  final bool running;
  final bool busy;
  final bool wide;
  final VoidCallback onOpen;
  final VoidCallback onRun;

  const _MatrixRow({
    required this.testCase,
    required this.outcome,
    required this.gradedAtIso,
    required this.open,
    required this.running,
    required this.busy,
    required this.wide,
    required this.onOpen,
    required this.onRun,
  });

  @override
  State<_MatrixRow> createState() => _MatrixRowState();
}

class _MatrixRowState extends State<_MatrixRow> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final outcome = widget.outcome;
    final tone = outcome == null
        ? CumulusTone.neutral
        : outcome.passed
        ? CumulusTone.success
        : CumulusTone.danger;
    // A failure tints its row. Ungraded does not: not having run a case is
    // not the case telling you something.
    final failing = outcome != null && !outcome.passed;
    final tint = widget.open
        ? CumulusColors.surfaceMuted.withValues(alpha: 0.7)
        : failing
        ? tone.background.withValues(alpha: _hover ? 0.9 : 0.5)
        : _hover
        ? CumulusColors.surfaceMuted.withValues(alpha: 0.55)
        : Colors.transparent;

    return MouseRegion(
      cursor: outcome == null
          ? MouseCursor.defer
          : SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: outcome == null ? null : widget.onOpen,
        behavior: HitTestBehavior.opaque,
        child: Container(
          color: tint,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 9),
          child: Row(
            children: [
              SizedBox(
                width: _evalResultW,
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: StatusBadge(
                    outcome == null
                        ? 'ungraded'
                        : outcome.passed
                        ? 'pass'
                        : 'fail',
                    tone: tone,
                    dense: true,
                    uppercase: true,
                  ),
                ),
              ),
              const SizedBox(width: _evalGap),
              Expanded(
                flex: 5,
                child: Text.rich(
                  TextSpan(
                    children: proseSpans(
                      widget.testCase.name,
                      CumulusText.rowTitle,
                    ),
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: CumulusText.rowTitle,
                ),
              ),
              if (widget.wide) ...[
                const SizedBox(width: _evalGap),
                Expanded(
                  flex: 6,
                  child: Text.rich(
                    TextSpan(
                      children: proseSpans(
                        widget.testCase.summary.isEmpty
                            ? widget.testCase.id
                            : widget.testCase.summary,
                        CumulusText.meta,
                      ),
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.meta,
                  ),
                ),
              ],
              const SizedBox(width: _evalGap),
              SizedBox(
                width: _evalWhenW,
                child: Text(
                  widget.gradedAtIso.isEmpty
                      ? 'never'
                      : agoIso(widget.gradedAtIso),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    fontSize: 11.5,
                    color: widget.gradedAtIso.isEmpty
                        ? CumulusColors.inkMuted
                        : CumulusColors.inkSecondary,
                  ),
                ),
              ),
              const SizedBox(width: _evalGap),
              SizedBox(
                width: _evalActionsW,
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    // The failure counts the row used to carry as badges are
                    // one click away, under the chevron; what belongs in an
                    // actions column is an action.
                    if (outcome != null && outcome.failures.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(right: 4),
                        child: Text(
                          '${outcome.failures.length}',
                          style: const TextStyle(
                            fontSize: 11.5,
                            fontWeight: FontWeight.w600,
                            color: CumulusColors.danger,
                          ),
                        ),
                      ),
                    IconButton(
                      tooltip: 'Run this case',
                      visualDensity: VisualDensity.compact,
                      onPressed: widget.busy ? null : widget.onRun,
                      icon: widget.running
                          ? const SizedBox(
                              width: 13,
                              height: 13,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.play_arrow, size: 16),
                      color: CumulusColors.inkSecondary,
                    ),
                    Icon(
                      widget.open ? Icons.expand_less : Icons.expand_more,
                      size: 18,
                      color: outcome == null
                          ? Colors.transparent
                          : CumulusColors.inkMuted,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// The result
// ---------------------------------------------------------------------------

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
      'reported' => 'the run reported the numbers the case names',
      'task_raised' => 'a task was raised on the desk the case names',
      'claimed_by' => 'the field the case names is claimed by one source',
      _ => kind,
    };
  }
  final it = '`$expected`';
  // `claimed_by` is `Type.field=source`, and reading it back as two halves is
  // the difference between a rule somebody can check and a token pair.
  final at = expected.indexOf('=');
  final claimed = at < 0
      ? 'every claim on $it comes from one source'
      : 'every claim on `${expected.substring(0, at)}` came from '
            '`${expected.substring(at + 1)}`';
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
    'reported' => 'the run reported $it',
    'task_raised' => 'a task was raised on $it',
    'claimed_by' => claimed,
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
