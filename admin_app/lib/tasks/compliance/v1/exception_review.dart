/// The four compliance exceptions, as the person holding the record meets them.
///
/// `credential-tracking` raises at most four Tasks per window — one per
/// exception class, carrying its rows — and none of them has a proposal. That
/// is not an omission. **There is no Action in this system that deletes a
/// compliance record, corrects a date, or withdraws a permission on a
/// reviewer's behalf.** Every one of these four questions is answered by a
/// person supplying a fact, and the flow's own `resumes` line says so; this
/// screen quotes that line rather than inventing a promise of its own.
///
/// What a person can do here is exactly one thing, and it is the thing that
/// actually settles three of the four classes: **write the date down as it
/// really is.** A date typed here becomes a `human:` claim on the row, and
/// `human` outranks the issuing system in the ontology's PRECEDENCE — so the
/// next window reads the corrected date instead of the one the feed keeps
/// re-asserting, without anybody editing the feed and without the record being
/// removed. The row that was unreadable becomes a dated row; the row retained
/// past the horizon stays retained, because nothing deletes it.
///
/// The date is shown **twice on purpose**: as the issuing system wrote it,
/// verbatim, beside an input. The first is evidence — `31/02/25`, `n/a`, an
/// empty string — and it is the thing a person is being asked to read. The
/// second is the answer. Collapsing them into one prefilled box would quietly
/// throw away what the source actually said, which on this screen is most of
/// the question.
library;

import 'package:flutter/material.dart';

import '../../../models/task.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';

/// The four classes the flow names, in its own spelling.
const String missingDateClass = 'missing-or-unreadable-date';
const String unresolvedHolderClass = 'unresolved-holder';
const String retainedPastHorizonClass = 'retained-past-horizon';
const String terminatedHolderClass = 'terminated-holder-still-active';

/// The evidence rows that describe the class rather than a record.
///
/// A set rather than a count for the reason the field-documents screen gives:
/// only `retained-past-horizon` carries a `Retention` row, so "the first
/// three" would promote a record to a heading on the other three classes.
const Set<String> _headerLabels = {'Class', 'Rows', 'Retention'};

/// A date the graph can key on. Anything else is left with the person rather
/// than sent — see [_ExceptionReviewState._payload].
final RegExp _isoDate = RegExp(r'^\d{4}-\d{2}-\d{2}$');

/// The class this task's `caseRef` names — `compliance-exception:<class>`.
String exceptionClassOf(String caseRef) {
  final at = caseRef.lastIndexOf(':');
  if (at < 0 || at + 1 >= caseRef.length) return '';
  return caseRef.substring(at + 1);
}

/// What each class is actually asking, in one sentence.
///
/// Per class rather than one sentence for all four, because they are four
/// different questions and only three of them are answered by a date. A class
/// this build does not know gets the honest fallback: the rows are shown, the
/// date input still works, and nothing claims to know what the finding means.
(String, String) exceptionCopy(String name) => switch (name) {
  missingDateClass => (
    'A date nothing could read',
    'The issuing system wrote something in the expiry field that is not a '
        'date, or wrote nothing at all. A row with no readable date is not a '
        'row that expired today — it is a row nobody can run a notice '
        'schedule against, which is why it is here rather than in the sweep. '
        'Reading the certificate and typing the date is the answer.',
  ),
  unresolvedHolderClass => (
    'A permission whose holder is nobody',
    'The credential names a person the workforce does not have. Correcting '
        'the date does not answer this one — the holder does — but if the '
        'date is also wrong, fixing it here is still worth doing while you '
        'have the record open.',
  ),
  retainedPastHorizonClass => (
    'Held past the retention horizon',
    'These records are older than the governed retention window. **Nothing '
        'on this screen deletes them**, and nothing in this system does: the '
        'finding is that they are still held, and the answer is a disposition '
        'decision made where dispositions are made.',
  ),
  terminatedHolderClass => (
    'A live permission on somebody who has left',
    'The credential is still active and its holder is no longer employed. '
        'The answer is to withdraw the permission in the system that issued '
        'it; nothing here withdraws it, and a corrected date would only '
        'change when it lapses on its own.',
  ),
  _ => (
    'A finding this console does not know',
    'The flow raised a class this build has no purpose-made copy for — the '
        'deployment is ahead of the console. The rows are below exactly as '
        'the flow wrote them, and the date correction still travels, because '
        'that part of the answer does not depend on knowing the class.',
  ),
};

/// One record on the aggregate, and the date somebody typed for it.
class _RecordRow {
  /// The flow's own label for the record. **The payload is keyed by this** —
  /// it is the string the flow wrote and the string it can look back up.
  final String label;

  /// The whole detail line, shown as written.
  final String detail;

  /// The date the issuing system wrote, pulled out of the detail line so it
  /// can be put beside the input. Empty when the flow wrote no `date` segment.
  final String sourceDate;

  final TextEditingController corrected;

  _RecordRow({
    required this.label,
    required this.detail,
    required this.sourceDate,
  }) : corrected = TextEditingController(
         // Seeded only when the source's own text is already a date the graph
         // could key on. Seeding from an unreadable string would put a value
         // in the answer box that nobody typed and nothing can parse.
         text: _isoDate.hasMatch(sourceDate) ? sourceDate : '',
       );

  String get _seed => _isoDate.hasMatch(sourceDate) ? sourceDate : '';

  String get typed => corrected.text.trim();

  bool get changed => typed != _seed;

  /// Typed, changed, and shaped like a date. Anything else stays on screen.
  bool get sendable => changed && _isoDate.hasMatch(typed);

  /// Typed something that is not a date. Worth saying, because the alternative
  /// is a correction that silently does not travel.
  bool get malformed => typed.isNotEmpty && !_isoDate.hasMatch(typed);

  void dispose() => corrected.dispose();
}

class ExceptionReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const ExceptionReview({super.key, required this.api, required this.task});

  @override
  State<ExceptionReview> createState() => _ExceptionReviewState();
}

class _ExceptionReviewState extends State<ExceptionReview> {
  final _note = TextEditingController();
  final List<_RecordRow> _rows = [];
  final List<TaskEvidence> _context = [];

  late final String _class = exceptionClassOf(widget.task.caseRef);

  @override
  void initState() {
    super.initState();
    for (final row in widget.task.evidence) {
      if (_headerLabels.contains(row.label)) {
        _context.add(row);
        continue;
      }
      _rows.add(_RecordRow(
        label: row.label,
        detail: row.value,
        sourceDate: _dateSegment(row.value),
      ));
    }
  }

  @override
  void dispose() {
    for (final row in _rows) {
      row.dispose();
    }
    _note.dispose();
    super.dispose();
  }

  /// The `date <text>` segment of a detail line, as the source wrote it.
  ///
  /// The flow joins the segments with ` · ` and puts the date behind a `date `
  /// prefix, which is what makes it findable without re-parsing the whole
  /// sentence. A line with no such segment yields the empty string, and the
  /// screen then says the source wrote nothing rather than guessing.
  static String _dateSegment(String value) {
    for (final part in value.split(' · ')) {
      final trimmed = part.trim();
      if (trimmed.startsWith('date ')) return trimmed.substring(5).trim();
    }
    return '';
  }

  bool get _dirty => _rows.any((row) => row.changed);

  /// The dates a person actually changed, keyed by the flow's own row label.
  ///
  /// Only well-formed dates travel. A half-typed `2026-0` is not a claim about
  /// when a credential expires, and sending it would put a `human:` claim on
  /// the row that outranks the feed and says nothing — which is strictly worse
  /// than the unreadable date the row already has.
  Map<String, dynamic> _payload() {
    final dates = <String, String>{};
    for (final row in _rows) {
      if (row.sendable) dates[row.label] = row.typed;
    }
    return dates.isEmpty ? const {} : {'dates': dates};
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
    final (title, body) = exceptionCopy(_class);
    final malformed = _rows.where((row) => row.malformed).length;
    return ReviewFrame(
      eyebrow: 'Compliance exception · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'Each row shows the expiry date as the issuing system wrote it, '
          'beside a box for what it should say. Nothing here deletes a record.',
      footer: DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: 'Record these dates',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Lede(title: title, body: body),
          const SizedBox(height: 16),
          _precedence(),
          const SizedBox(height: 18),
          if (_context.isNotEmpty) ...[
            const FieldLabel(
              'What the window found',
              hint: 'the finding, before the records it found it on',
            ),
            for (final row in _context)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 96,
                      child: Text(row.label, style: CumulusText.statLabel),
                    ),
                    Expanded(child: Text(row.value, style: CumulusText.body)),
                  ],
                ),
              ),
            const SizedBox(height: 18),
          ],
          FieldLabel(
            'The records',
            hint: '${_rows.length} row(s) — one question, asked once',
          ),
          if (_rows.isEmpty)
            Text(
              'The Task carries no record rows. The finding above is the whole '
              'of what it says.',
              style: CumulusText.body,
            ),
          for (final row in _rows)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _RecordCard(row: row, onChanged: () => setState(() {})),
            ),
          if (malformed > 0) ...[
            const SizedBox(height: 6),
            Text(
              '$malformed row(s) hold something that is not a date. Those will '
              'not travel — a claim the graph cannot key on is not a '
              'correction — so either finish them as YYYY-MM-DD or clear them.',
              style: CumulusText.meta.copyWith(color: CumulusColors.warn),
            ),
          ],
          const SizedBox(height: 20),
          _resumes(),
        ],
      ),
    );
  }

  /// Why typing a date here is worth anything, stated in the vocabulary the
  /// graph actually uses.
  Widget _precedence() => Container(
    width: double.infinity,
    padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
    decoration: BoxDecoration(
      color: CumulusTone.accent.background,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('A DATE YOU TYPE HERE IS A `HUMAN` CLAIM',
            style: CumulusText.statLabel),
        const SizedBox(height: 7),
        Text(
          'It is written as a claim sourced to you, and `human` outranks the '
          'issuing system in PRECEDENCE — so the next window reads your date '
          'rather than the one the feed keeps re-asserting, and the feed is '
          'not edited, overridden or silenced. The original stays on the '
          'record underneath, which is what makes this reversible.',
          style: CumulusText.body,
        ),
        const SizedBox(height: 8),
        Text(
          'Nothing on this screen deletes a compliance record, withdraws a '
          'permission, or removes a row held past its retention horizon. '
          'There is no Action in this system that does any of those on a '
          'reviewer’s behalf.',
          style: CumulusText.body,
        ),
      ],
    ),
  );

  /// The flow's own account of what happens next, quoted.
  Widget _resumes() {
    if (widget.task.resumes.isEmpty) {
      return Text(
        'The Task says nothing about what resumes. Take that literally: this '
        'is an aggregate finding with no suspended run behind it.',
        style: CumulusText.meta,
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const FieldLabel('What the flow says happens', hint: 'its own words'),
        Text('“${widget.task.resumes}”', style: CumulusText.body),
      ],
    );
  }
}

/// One record: what it is, what the source wrote, and what it should say.
class _RecordCard extends StatelessWidget {
  final _RecordRow row;
  final VoidCallback onChanged;

  const _RecordCard({required this.row, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 13),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(
          color: row.changed ? CumulusColors.accent : CumulusColors.border,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(row.label, style: CumulusText.rowTitle),
                    if (row.detail.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(row.detail, style: CumulusText.meta),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 14),
              // The source's own text, never normalised. This is the evidence
              // half of the row: `31/02/25` and an empty field are different
              // findings and both have to survive to the screen intact.
              SizedBox(
                width: 150,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('AS WRITTEN', style: CumulusText.statLabel),
                    const SizedBox(height: 4),
                    Text(
                      row.sourceDate.isEmpty
                          ? 'nothing at all'
                          : '“${row.sourceDate}”',
                      style: row.sourceDate.isEmpty
                          ? CumulusText.meta
                          : CumulusText.mono,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                width: 168,
                child: TextField(
                  controller: row.corrected,
                  onChanged: (_) => onChanged(),
                  decoration: InputDecoration(
                    labelText: 'Expires on',
                    hintText: 'YYYY-MM-DD',
                    isDense: true,
                    errorText: row.malformed ? 'not a date' : null,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// What the class is asking, before it asks it.
class _Lede extends StatelessWidget {
  final String title;
  final String body;

  const _Lede({required this.title, required this.body});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: CumulusTone.warn.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: CumulusText.rowTitle.copyWith(
              color: CumulusTone.warn.foreground,
            ),
          ),
          const SizedBox(height: 6),
          Text(body, style: CumulusText.body),
        ],
      ),
    );
  }
}
