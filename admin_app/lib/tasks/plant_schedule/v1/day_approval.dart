/// A plant department's day, as the supervisor who signs it meets it —
/// department-scheduling v1.
///
/// This is the one screen in the plant set where approving *writes something*.
/// The day goes to `approved`, claimed as `human:` so no later re-fill can
/// quietly un-approve a day somebody signed — and the schedule export, which
/// answers 409 for a pending day, starts answering for this one. That is the
/// whole stake, and the screen says it rather than making a person infer it
/// from a green button.
///
/// So everything a signature rests on is on the page, and each piece is
/// **shown as its own arithmetic** rather than as a figure to trust:
///
/// - the rows, with their hours and the rule that placed them, editable;
/// - the total drawn against the goal and the absolute max with the fudge band
///   the governed page allows, recomputed live so the bar moves while somebody
///   edits;
/// - the incentive as `start + rate/h × hours = answer`, with the crossing
///   point named, so "out of the hole" is a claim a supervisor can check
///   rather than a cell nobody can audit;
/// - what was deferred, with the governed clause and the sentence;
/// - the headcount **and its age**, warned about rather than hidden when the
///   feed is behind.
///
/// **The proposal's `fields` are not [FieldVerdict]s.** `TaskProposal.fields`
/// is untyped by contract — the flow that raised the task is the only thing
/// that interprets it — and this flow puts one row of the day's schedule
/// there: a path, a job number and label, the hours, the row kind, who placed
/// it and on what basis. Read through `.fields` those would parse into
/// verdicts with no path, no status and no reason: not an error, which is
/// worse than one, because the screen would render a plausible empty question.
/// So this screen reads [TaskProposal.rawFields], and
/// `test/day_approval_contract_test.dart` pins that as a property rather than
/// as a comment.
///
/// **The numbers a bar needs are not on the proposal.** The goal, the absolute
/// max, the fudge, the incentive figures, the deferred shares and the
/// headcount all live on the schedule cell, so the screen fetches the week and
/// takes the cell out of it. When that fetch fails, or the week has no such
/// cell, the screen degrades to the proposal's rows and the Task's own
/// evidence and **says the capacity figures could not be loaded**. It never
/// draws a bar: a capacity bar with invented bounds is a picture of a governed
/// limit that nothing governs.
library;

import 'package:flutter/material.dart';

import '../../../models/task.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';
import '../models.dart';

/// The department and date a `<department-slug>:<YYYY-MM-DD>` day key names.
///
/// Split on the **last** colon: a date carries none, so anything before it is
/// the department however the slug is spelled. Returns two empty strings for a
/// case ref that is not a day key at all, which the screen treats as "no cell
/// can be fetched" rather than as an error — the rows are still decidable.
(String, String) dayKeyParts(String caseRef) {
  final at = caseRef.lastIndexOf(':');
  if (at <= 0 || at + 1 >= caseRef.length) return ('', '');
  return (caseRef.substring(0, at), caseRef.substring(at + 1));
}

/// `YYYY-Www` for a date — ISO-8601 week-numbering, the export's own parameter.
///
/// Implemented rather than approximated, because the two easy wrong answers
/// are both wrong at exactly the moment this screen is used: a week numbered
/// from January 1 disagrees with the server for the first days of most years,
/// and a week numbered from the Sunday disagrees every week of every year.
///
/// The rule is the Thursday rule. An ISO week belongs to the year its
/// **Thursday** falls in, and week 1 is the week holding January 4 —
/// equivalently, the week whose Thursday is the first Thursday of the year. So:
/// step from the date to the Thursday of its own week, take *that* year, and
/// count whole weeks from its January 1.
///
/// Computed in UTC so a daylight-saving boundary cannot shift a date by a day
/// halfway through the arithmetic.
String isoWeekOf(DateTime date) {
  final day = DateTime.utc(date.year, date.month, date.day);
  // `weekday` is Monday = 1 … Sunday = 7, so Thursday is 4.
  final thursday = day.add(Duration(days: 4 - day.weekday));
  final firstOfYear = DateTime.utc(thursday.year, 1, 1);
  final week = (thursday.difference(firstOfYear).inDays / 7).floor() + 1;
  return '${thursday.year}-W${week.toString().padLeft(2, '0')}';
}

/// The ISO week a `YYYY-MM-DD` string falls in, or empty when it is not a date.
String isoWeekForDate(String date) {
  final parsed = DateTime.tryParse(date);
  return parsed == null ? '' : isoWeekOf(parsed);
}

/// One row of the day, and the edit somebody has made to it.
///
/// Built from the proposal's raw map with a default for every key. A task
/// already raised, whose payload predates a field, must still open a screen:
/// a parse that threw here would take out the day rather than one row.
class _RowDraft {
  /// `rows.<key>` — the path as the flow wrote it, and the identity the
  /// correction travels under.
  final String path;
  final String jobNumber;
  final String jobLabel;
  final String rowKind;

  /// `flow` | `human`. A row a supervisor placed by hand and a row the fill
  /// computed are the same shape, and the screen must never draw them the
  /// same way — a supervisor re-deciding their own placement should know it
  /// was theirs.
  final String placedBy;

  /// Why the row is here and why for these hours — the rule that placed it,
  /// named so it can be argued with.
  final String basis;

  final double originalHours;
  final TextEditingController hours;
  bool removed = false;

  /// The bare row key the flow's applier is keyed on — `rows.260118` →
  /// `260118`. The path is what the proposal carries; the key is what the
  /// correction has to travel under, and a payload that sent the path would
  /// have every row refused.
  String get key =>
      path.startsWith('rows.') ? path.substring('rows.'.length) : path;

  _RowDraft({
    required this.path,
    required this.jobNumber,
    required this.jobLabel,
    required this.rowKind,
    required this.placedBy,
    required this.basis,
    required this.originalHours,
  }) : hours = TextEditingController(text: _trim(originalHours));

  factory _RowDraft.fromRaw(Map<String, dynamic> raw) => _RowDraft(
    path: raw['path'] as String? ?? '',
    jobNumber: raw['job_number'] as String? ?? '',
    jobLabel: raw['job_label'] as String? ?? '',
    rowKind: raw['row_kind'] as String? ?? '',
    placedBy: raw['placed_by'] as String? ?? '',
    basis: raw['basis'] as String? ?? '',
    originalHours: (raw['labor_hours'] as num? ?? 0).toDouble(),
  );

  bool get isHuman => placedBy == 'human';

  double get hoursValue => double.tryParse(hours.text.trim()) ?? 0;

  bool get edited => (hoursValue - originalHours).abs() > 0.0001;

  bool get touched => removed || edited;

  String get label => jobLabel.isNotEmpty
      ? jobLabel
      : (jobNumber.isNotEmpty ? jobNumber : path);

  void dispose() => hours.dispose();

  /// `12.5` rather than `12.50`, and `16` rather than `16.0` — the plant's own
  /// rendering of an hours figure.
  static String _trim(double value) {
    final text = value.toStringAsFixed(2);
    return text
        .replaceFirst(RegExp(r'0+$'), '')
        .replaceFirst(RegExp(r'\.$'), '');
  }
}

class DayApproval extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const DayApproval({super.key, required this.api, required this.task});

  @override
  State<DayApproval> createState() => _DayApprovalState();
}

class _DayApprovalState extends State<DayApproval> {
  final _note = TextEditingController();
  final List<_RowDraft> _rows = [];

  ScheduleDayCell? _cell;

  /// Why the capacity figures are not on screen, when they are not. Empty
  /// while the fetch is still in flight and after one that landed.
  String _cellProblem = '';
  bool _cellLoaded = false;

  late final (String, String) _day = dayKeyParts(widget.task.caseRef);
  String get _department => _day.$1;
  String get _date => _day.$2;
  late final String _week = isoWeekForDate(_date);

  @override
  void initState() {
    super.initState();
    for (final raw in widget.task.proposal?.rawFields ?? const []) {
      _rows.add(_RowDraft.fromRaw(raw));
    }
    _loadCell();
  }

  @override
  void dispose() {
    for (final row in _rows) {
      row.dispose();
    }
    _note.dispose();
    super.dispose();
  }

  /// The cell behind the bar, fetched by the week the date falls in.
  ///
  /// Every failure lands in [_cellProblem] with a sentence saying which one it
  /// was, because they are different facts: a case ref that is not a day key,
  /// a week the server would not serve, and a week it served that has no such
  /// cell are three different things to tell a supervisor about a bar they
  /// cannot see.
  Future<void> _loadCell() async {
    if (_department.isEmpty || _date.isEmpty || _week.isEmpty) {
      setState(() {
        _cellLoaded = true;
        _cellProblem =
            'This task’s case ref (“${widget.task.caseRef}”) is not a '
            '`<department>:<YYYY-MM-DD>` day key, so there is no week to ask '
            'for and no cell to read the capacity figures from.';
      });
      return;
    }
    try {
      final screen = await widget.api.plantScheduleScreen(week: _week);
      if (!mounted) return;
      final cell = screen.cellFor(_department, _date);
      setState(() {
        _cellLoaded = true;
        _cell = cell;
        _cellProblem = cell == null
            ? 'The week $_week loaded, but it holds no cell for '
                '$_department on $_date — so the goal, the absolute max, the '
                'fudge band, the incentive figures and the headcount are not '
                'available to draw.'
            : '';
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _cellLoaded = true;
        _cellProblem =
            'The plant schedule for $_week did not load, so the capacity '
            'figures could not be read: ${e.message}';
      });
    }
  }

  /// The total as the rows now stand. The bar reads this rather than the
  /// cell's stored total, which is what makes it move while somebody edits.
  double get _liveTotal => _rows
      .where((row) => !row.removed)
      .fold<double>(0, (sum, row) => sum + row.hoursValue);

  double get _proposedTotal =>
      _rows.fold<double>(0, (sum, row) => sum + row.originalHours);

  bool get _dirty => _rows.any((row) => row.touched);

  /// The rows a person touched, each stamped as theirs, and the ones removed.
  ///
  /// Only the touched rows. The flow's own sentence for a correction is that
  /// it "re-fills what is left around the rows you touched" — an untouched row
  /// is not a supervisor's placement and stamping it `human` would freeze a
  /// machine's guess against every later fill.
  ///
  /// **A live mismatch worth knowing about.** The applier this travels to —
  /// `_apply_correction` in
  /// `server/app/tasks/department_scheduling/v1/flow.py` — reads `rows` as a
  /// *map* keyed by the bare row key (`260118`), and `removed` as a list of
  /// bare row keys. The shape below is the one this console was specified
  /// against: a list of objects each carrying its `rows.<key>` path. Both
  /// halves are written down here rather than quietly reconciled on one side,
  /// because a seam is not fixed by whichever end is closer to hand.
  /// The correction, in the shape the flow's own applier reads.
  ///
  /// Pinned to `department_scheduling/v1/flow.py::_apply_correction`, which is
  /// the only thing that ever interprets this: `rows` is a **map keyed by the
  /// bare row key** and `removed` is a **list of bare row keys**. The proposal
  /// carries those keys as `rows.<key>` paths, so the prefix is stripped here
  /// — and it has to be stripped, because a key that day does not carry is
  /// *refused and counted* rather than created. A payload sent under the path
  /// would have every row silently refused and the day re-filled as though
  /// nobody had touched it: a correction that vanished, with an approval
  /// screen reporting success.
  ///
  /// `placed_by` is deliberately **not** sent. The applier stamps `human` on
  /// every row it accepts, and a screen asserting it too would be a second
  /// claim about whose judgment this was — one the server would have to agree
  /// with rather than make.
  Map<String, dynamic> _payload() {
    final rows = <String, dynamic>{};
    final removed = <String>[];
    for (final row in _rows) {
      if (row.removed) {
        removed.add(row.key);
        continue;
      }
      if (!row.edited) continue;
      rows[row.key] = {'labor_hours': row.hoursValue};
    }
    return {
      if (rows.isNotEmpty) 'rows': rows,
      if (removed.isNotEmpty) 'removed': removed,
    };
  }

  void _decide(String verdict) {
    Navigator.of(context).pop(ReviewOutcome(
      verdict: verdict,
      note: _note.text.trim(),
      payload: verdict == 'corrected' ? _payload() : const {},
    ));
  }

  @override
  Widget build(BuildContext context) {
    final cell = _cell;
    return ReviewFrame(
      eyebrow: 'Day approval · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Approving marks this day approved and makes it exportable. Editing '
          'the hours or dropping a row stamps it as yours and re-fills what is '
          'left around it.',
      footer: DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: 'Apply and re-fill',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _identity(),
          const SizedBox(height: 18),
          if (!_cellLoaded)
            const CumulusLoading()
          else if (cell == null)
            _degraded()
          else ...[
            _CapacityBar(cell: cell, total: _liveTotal),
            const SizedBox(height: 18),
            _incentive(cell),
            const SizedBox(height: 18),
            _headcount(cell),
          ],
          const SizedBox(height: 22),
          _rowsSection(),
          if (cell != null && cell.deferred.isNotEmpty) ...[
            const SizedBox(height: 22),
            _deferred(cell),
          ],
          const SizedBox(height: 22),
          _unlocks(cell),
          const SizedBox(height: 18),
          _evidence(),
        ],
      ),
    );
  }

  Widget _identity() => Wrap(
    spacing: 10,
    runSpacing: 10,
    children: [
      MetaPill(
        _department.isEmpty ? widget.task.caseRef : _department,
        tone: CumulusTone.teal,
      ),
      if (_date.isNotEmpty) MetaPill(_date),
      if (_week.isNotEmpty) MetaPill('week $_week'),
      MetaPill('${_rows.length} row(s) proposed'),
      if (widget.task.proposal != null)
        MetaPill(
          widget.task.proposal!.action,
          tone: CumulusTone.accent,
        ),
    ],
  );

  /// Said plainly, once, where the bar would have been.
  Widget _degraded() => Container(
    width: double.infinity,
    padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
    decoration: BoxDecoration(
      color: CumulusTone.warn.background,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('THE CAPACITY FIGURES COULD NOT BE LOADED',
            style: CumulusText.statLabel),
        const SizedBox(height: 7),
        Text(_cellProblem, style: CumulusText.body),
        const SizedBox(height: 8),
        Text(
          'No bar is drawn, and none should be: a capacity bar needs the goal, '
          'the absolute max and the fudge band, and a bar drawn from numbers '
          'this screen made up would be a picture of a governed limit that '
          'nothing governs. The rows below and the Task’s own evidence are '
          'what is known, and the day is still decidable on them.',
          style: CumulusText.body,
        ),
        const SizedBox(height: 8),
        Text(
          'Rows as proposed total '
          '${_hours(_proposedTotal)} h; as they now stand, '
          '${_hours(_liveTotal)} h.',
          style: CumulusText.meta,
        ),
        if (_cellProblem.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: TextButton.icon(
              onPressed: _loadCell,
              icon: const Icon(Icons.refresh, size: 15),
              label: const Text('Try the week again'),
            ),
          ),
      ],
    ),
  );

  // -- the incentive, as arithmetic -----------------------------------------

  /// `start + rate/h × hours = answer`, with the crossing point named.
  ///
  /// The flow's own sentence is quoted underneath rather than replaced,
  /// because the arithmetic drawn here is this screen's rendering of the
  /// figures and that sentence is the flow's own account of them. When a
  /// supervisor's edit moves the hours, the same figures are run again at the
  /// new total and labelled as *this screen's* arithmetic — never as something
  /// the flow said, which it did not.
  Widget _incentive(ScheduleDayCell cell) {
    final start = cell.incentiveStartUsd;
    final rate = cell.incentiveRateUsdPerHour;
    final crossing = cell.incentiveCrossingHours;
    final moved = (_liveTotal - cell.totalHours).abs() > 0.0001;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 13),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.borderStrong),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Expanded(
                child: FieldLabel(
                  'Incentive',
                  hint: 'shown as its own arithmetic, so it can be disputed',
                ),
              ),
              StatusBadge(
                cell.outOfTheHole ? 'out of the hole' : 'still in the hole',
                tone: cell.outOfTheHole
                    ? CumulusTone.success
                    : CumulusTone.warn,
                dense: true,
              ),
            ],
          ),
          if (start == null || rate == null)
            Text(
              'The governed page carries no incentive figures for '
              '$_department, so there is no arithmetic to show. The flow’s own '
              'answer for this day is '
              '\$${cell.incentivePerHour.toStringAsFixed(2)} per hour.',
              style: CumulusText.body,
            )
          else ...[
            Text(
              '\$${start.toStringAsFixed(2)}'
              '  +  \$${rate.toStringAsFixed(2)}/h  ×  '
              '${_hours(cell.totalHours)} h  =  '
              '\$${cell.incentivePerHour.toStringAsFixed(2)} per hour',
              style: CumulusText.mono,
            ),
            if (moved) ...[
              const SizedBox(height: 6),
              Text(
                'At the total as you have it now — '
                '\$${start.toStringAsFixed(2)} + \$${rate.toStringAsFixed(2)}/h '
                '× ${_hours(_liveTotal)} h = '
                '\$${(start + rate * _liveTotal).toStringAsFixed(2)} per hour. '
                'That is this screen running the same two governed figures at '
                'your total, not a number the flow has computed; the flow '
                'recomputes it when the correction re-fills the day.',
                style: CumulusText.meta,
              ),
            ],
          ],
          const SizedBox(height: 8),
          Text(
            crossing == null
                ? 'The page states no crossing point for this department, so '
                    '“out of the hole” rests on the figures above and nothing '
                    'else.'
                : 'The governed figures cross zero at '
                    '${_hours(crossing)} h — below that the day is in the '
                    'hole, above it the incentive is positive.',
            style: CumulusText.body,
          ),
          if (cell.incentiveBasis.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('“${cell.incentiveBasis}”', style: CumulusText.meta),
          ],
        ],
      ),
    );
  }

  // -- headcount ------------------------------------------------------------

  /// The count and **its age**. The governed page says warn and proceed on a
  /// stale one, which is a different instruction from failing on it and a
  /// different instruction again from hiding the age.
  Widget _headcount(ScheduleDayCell cell) => Container(
    width: double.infinity,
    padding: const EdgeInsets.fromLTRB(14, 12, 14, 13),
    decoration: BoxDecoration(
      color: cell.headcountStale
          ? CumulusTone.warn.background
          : CumulusColors.surface,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      border: Border.all(
        color: cell.headcountStale
            ? Colors.transparent
            : CumulusColors.borderStrong,
      ),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Expanded(child: FieldLabel('Headcount')),
            if (cell.headcountStale)
              const StatusBadge('stale', tone: CumulusTone.warn, dense: true),
          ],
        ),
        Text(
          '${_hours(cell.headcount)} people'
          '${cell.headcountAsOf.isEmpty ? "" : " — as of ${cell.headcountAsOf}"}',
          style: CumulusText.body,
        ),
        if (cell.headcountStale) ...[
          const SizedBox(height: 6),
          Text(
            'The governed freshness window has passed, so this count is shown '
            'rather than trusted. The page says warn and proceed — it does not '
            'say stop — and the capacity row above was chosen against this '
            'number, which is why the age is worth a look before you sign.',
            style: CumulusText.body,
          ),
        ],
        if (cell.capacityRow.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('Capacity row chosen: ${cell.capacityRow}',
              style: CumulusText.meta),
        ],
      ],
    ),
  );

  // -- the rows -------------------------------------------------------------

  Widget _rowsSection() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      FieldLabel(
        'The day’s rows',
        hint: 'edit the hours or drop a row — either stamps it as yours',
      ),
      if (_rows.isEmpty)
        Text(
          widget.task.proposal == null
              ? 'This task carries no proposal, so there are no rows to show. '
                  'That is not an empty day — it is a task raised without one.'
              : 'The proposal carries no rows. The fill placed nothing on this '
                  'day, which is a real state and a different one from a day '
                  'nobody scheduled.',
          style: CumulusText.body,
        ),
      for (final row in _rows)
        Padding(
          padding: const EdgeInsets.only(bottom: 9),
          child: _RowEditor(
            row: row,
            onChanged: () => setState(() {}),
            onRemove: () => setState(() => row.removed = !row.removed),
          ),
        ),
      if (_rows.isNotEmpty) ...[
        const SizedBox(height: 6),
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            Text('Total as it stands', style: CumulusText.statLabel),
            const SizedBox(width: 14),
            Text('${_hours(_liveTotal)} h', style: CumulusText.statValue),
            const SizedBox(width: 18),
            Text(
              'proposed ${_hours(_proposedTotal)} h',
              style: CumulusText.meta,
            ),
          ],
        ),
      ],
    ],
  );

  // -- deferred -------------------------------------------------------------

  /// What the fill did not place, with the clause and the sentence.
  ///
  /// Both, because "why is 260118 not on Tuesday" has to answer as a governed
  /// clause and as English — one of those is arguable and the other is
  /// readable, and a supervisor needs whichever one they are about to use.
  Widget _deferred(ScheduleDayCell cell) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      FieldLabel(
        'Deferred',
        hint: '${cell.deferred.length} share(s) the fill did not place — the '
            'next window re-raises them',
      ),
      for (final row in cell.deferred)
        Padding(
          padding: const EdgeInsets.only(bottom: 6),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 96,
                child: Text(row.jobNumber, style: CumulusText.mono),
              ),
              SizedBox(
                width: 170,
                child: StatusBadge(
                  row.clause.isEmpty ? 'no clause named' : row.clause,
                  tone: CumulusTone.neutral,
                  dense: true,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(child: Text(row.reason, style: CumulusText.body)),
              if (row.laborHours > 0)
                Text('${_hours(row.laborHours)} h', style: CumulusText.meta),
            ],
          ),
        ),
    ],
  );

  // -- what approving unlocks ----------------------------------------------

  Widget _unlocks(ScheduleDayCell? cell) => Container(
    width: double.infinity,
    padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
    decoration: BoxDecoration(
      color: CumulusTone.accent.background,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('WHAT APPROVING UNLOCKS', style: CumulusText.statLabel),
        const SizedBox(height: 7),
        Text(
          'Nothing has left this platform. The day is written at '
          '`pending_approval`, and the schedule export refuses to answer for a '
          'pending day — approving is what makes it exportable, and it is '
          'recorded as yours: a `human` claim, which no later re-fill may '
          'overwrite. Rejecting leaves the day unapproved and unexportable.',
          style: CumulusText.body,
        ),
        if (cell != null && cell.exportUrl.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(
            '${cell.exportable ? "Exportable now: " : "Would answer at: "}'
            '${cell.exportUrl}',
            style: CumulusText.mono,
          ),
        ],
        if (widget.task.proposal?.summary.isNotEmpty ?? false) ...[
          const SizedBox(height: 8),
          Text('“${widget.task.proposal!.summary}”', style: CumulusText.meta),
        ],
        if (widget.task.resumes.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text('“${widget.task.resumes}”', style: CumulusText.meta),
        ],
      ],
    ),
  );

  /// The Task's own evidence, folded in underneath. It is the flow's account
  /// of the same day, and it is the whole of what is known when the cell did
  /// not load.
  Widget _evidence() {
    if (widget.task.evidence.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const FieldLabel('What the run recorded', hint: 'the Task’s own rows'),
        for (final row in widget.task.evidence)
          Padding(
            padding: const EdgeInsets.only(bottom: 5),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 170,
                  child: Text(row.label, style: CumulusText.statLabel),
                ),
                Expanded(child: Text(row.value, style: CumulusText.body)),
              ],
            ),
          ),
      ],
    );
  }
}

/// `28.5` rather than `28.50`, and `12` rather than `12.0`.
String _hours(double value) {
  final text = value.toStringAsFixed(2);
  return text.replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
}

// ---------------------------------------------------------------------------
// The bar
// ---------------------------------------------------------------------------

/// The day's total against every bound the governed page states.
///
/// Three marks, and each one is a different claim. The **goal** is what the
/// plant aims at. The **fudge band** is the latitude the page explicitly
/// allows past it — drawn as a band rather than as an alarm, because the page
/// says exceeding the goal within the fudge is accepted and is not an
/// exception. The **ceiling** is the smaller of the two governed maxima with
/// its fudge, and past it the day is over every bound the page states.
///
/// The fill is the **live** total, so it moves while somebody edits. The
/// cell's own `inFudgeBand` and `overCeiling` describe the day as the fill
/// left it, and where an edit has moved the day out of the state the fill
/// recorded, the bar says so — a supervisor should not have to reconcile a
/// green flag on the record with a red bar in front of them.
class _CapacityBar extends StatelessWidget {
  final ScheduleDayCell cell;
  final double total;

  const _CapacityBar({required this.cell, required this.total});

  @override
  Widget build(BuildContext context) {
    final scale = cell.barScale;
    final ceiling = cell.ceilingHours;
    final goal = cell.goalMaxHours;
    final absMax = cell.absMaxHours;

    final overCeiling = ceiling != null && total > ceiling;
    final inFudge = goal != null &&
        total > goal &&
        total <= goal + cell.fudgeHours;
    final tone = overCeiling
        ? CumulusTone.danger
        : inFudge
            ? CumulusTone.warn
            : CumulusTone.success;
    final drifted = overCeiling != cell.overCeiling ||
        inFudge != cell.inFudgeBand;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Expanded(
              child: FieldLabel(
                'Total against capacity',
                hint: 'the goal, the fudge the page allows, and the ceiling',
              ),
            ),
            Text('${_hours(total)} h', style: CumulusText.statValue),
          ],
        ),
        const SizedBox(height: 8),
        LayoutBuilder(
          builder: (context, constraints) {
            final width = constraints.maxWidth;
            double at(double hours) =>
                (hours / scale).clamp(0.0, 1.0) * width;
            return SizedBox(
              height: 34,
              child: Stack(
                children: [
                  Container(
                    height: 34,
                    decoration: BoxDecoration(
                      color: CumulusColors.surfaceMuted,
                      borderRadius: BorderRadius.circular(8),
                    ),
                  ),
                  // The fudge band, drawn behind the fill: it is a region of
                  // the scale, not a state of the day.
                  if (goal != null && cell.fudgeHours > 0)
                    Positioned(
                      left: at(goal),
                      width: (at(goal + cell.fudgeHours) - at(goal))
                          .clamp(0.0, width),
                      top: 0,
                      bottom: 0,
                      child: Container(color: CumulusTone.warn.background),
                    ),
                  Container(
                    height: 34,
                    width: at(total),
                    decoration: BoxDecoration(
                      color: tone.background,
                      borderRadius: BorderRadius.circular(8),
                    ),
                  ),
                  if (goal != null) _Mark(x: at(goal), label: 'goal'),
                  if (absMax != null) _Mark(x: at(absMax), label: 'abs max'),
                  if (ceiling != null)
                    _Mark(
                      x: at(ceiling),
                      label: 'ceiling',
                      tone: CumulusTone.danger,
                    ),
                ],
              ),
            );
          },
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 12,
          runSpacing: 4,
          children: [
            Text(
              goal == null ? 'no goal stated' : 'goal ${_hours(goal)} h',
              style: CumulusText.meta,
            ),
            Text(
              absMax == null
                  ? 'no absolute max stated'
                  : 'absolute max ${_hours(absMax)} h',
              style: CumulusText.meta,
            ),
            Text('fudge ${_hours(cell.fudgeHours)} h',
                style: CumulusText.meta),
            Text(
              ceiling == null
                  ? 'the page states no ceiling'
                  : 'ceiling ${_hours(ceiling)} h',
              style: CumulusText.meta,
            ),
            if (cell.belowCapacity)
              const StatusBadge('below capacity',
                  tone: CumulusTone.warn, dense: true),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          overCeiling
              ? 'This day is past every bound the page states, fudge included.'
              : inFudge
                  ? 'Past the goal and inside the latitude the page allows. '
                      'The page says that is accepted and is not an exception.'
                  : 'Inside the goal.',
          style: CumulusText.body.copyWith(color: tone.foreground),
        ),
        if (drifted) ...[
          const SizedBox(height: 6),
          Text(
            'The fill left this day at ${_hours(cell.totalHours)} h — '
            '${cell.overCeiling
                ? "over the ceiling"
                : cell.inFudgeBand
                    ? "in the fudge band"
                    : "inside the goal"}. '
            'Your edits have moved it out of that state, and the bar above is '
            'your total, not the one on the record.',
            style: CumulusText.meta,
          ),
        ],
        if (cell.stoppedAt.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text('Stopped at: ${cell.stoppedAt}', style: CumulusText.meta),
        ],
      ],
    );
  }
}

/// A governed bound, marked on the bar and named under it.
class _Mark extends StatelessWidget {
  final double x;
  final String label;
  final CumulusTone tone;

  const _Mark({
    required this.x,
    required this.label,
    this.tone = CumulusTone.neutral,
  });

  @override
  Widget build(BuildContext context) {
    return Positioned(
      left: x,
      top: 0,
      bottom: 0,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(width: 1.6, height: 22, color: tone.foreground),
          Text(
            label,
            style: CumulusText.statCaption.copyWith(
              fontSize: 9.5,
              color: tone.foreground,
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// One row
// ---------------------------------------------------------------------------

/// One row of the day: what it is, why it is here, and the two edits.
class _RowEditor extends StatelessWidget {
  final _RowDraft row;
  final VoidCallback onChanged;
  final VoidCallback onRemove;

  const _RowEditor({
    required this.row,
    required this.onChanged,
    required this.onRemove,
  });

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: row.removed ? 0.45 : 1,
      child: Container(
        padding: const EdgeInsets.fromLTRB(14, 11, 8, 12),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(
            color: row.touched ? CumulusColors.accent : CumulusColors.border,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(row.label, style: CumulusText.rowTitle),
                          ),
                          const SizedBox(width: 8),
                          if (row.rowKind.isNotEmpty && row.rowKind != 'job')
                            StatusBadge(row.rowKind, dense: true),
                          const SizedBox(width: 6),
                          // A row a supervisor placed and a row the fill
                          // computed are the same shape on the wire. They must
                          // never be the same shape on the screen.
                          StatusBadge(
                            row.isHuman ? 'placed by a person' : 'placed by the fill',
                            tone: row.isHuman
                                ? CumulusTone.violet
                                : CumulusTone.neutral,
                            dense: true,
                          ),
                        ],
                      ),
                      if (row.basis.isNotEmpty) ...[
                        const SizedBox(height: 3),
                        Text(row.basis, style: CumulusText.meta),
                      ],
                    ],
                  ),
                ),
                const SizedBox(width: 12),
                SizedBox(
                  width: 120,
                  child: TextField(
                    controller: row.hours,
                    enabled: !row.removed,
                    onChanged: (_) => onChanged(),
                    keyboardType: TextInputType.number,
                    decoration: InputDecoration(
                      labelText: 'Hours',
                      isDense: true,
                      helperText: row.edited
                          ? 'was ${_hours(row.originalHours)}'
                          : null,
                    ),
                  ),
                ),
                IconButton(
                  onPressed: onRemove,
                  icon: Icon(
                    row.removed ? Icons.undo : Icons.delete_outline,
                    size: 16,
                  ),
                  tooltip: row.removed
                      ? 'Keep this row on the day'
                      : 'Take this row off the day',
                ),
              ],
            ),
            if (row.touched) ...[
              const SizedBox(height: 6),
              Text(
                row.removed
                    ? 'Removed. The job comes out of the running for this day, '
                        'so the next fill does not simply put it back.'
                    : 'Edited. This row travels stamped `placed_by: human`, '
                        'which the re-fill works around and no later run may '
                        'overwrite.',
                style: CumulusText.meta.copyWith(
                  color: CumulusColors.accentInk,
                ),
              ),
            ],
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(row.path, style: CumulusText.mono),
            ),
          ],
        ),
      ),
    );
  }
}
