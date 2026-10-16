/// The shipping list as a supervisor should meet it — shipping-list-extraction v1.
///
/// A shipping list is seven page families, and a full reading of one is a form
/// with more than a hundred inputs in it. Handing that back to a supervisor is
/// handing back the machine's work: the run already knows which values it was
/// unsure of, which grids disagree with their own printed totals, which
/// required fields are absent from a page family that is plainly present, and
/// which computed figures came out beyond their department's governed band. It
/// knew all of that before it raised the Task. So the screen asks about the
/// three or four fields in doubt and folds the rest away.
///
/// This is deliberately the **same contract** accounts-payable v8 uses —
/// [FieldVerdict]: a canonical fact path, a label, the value as read, a
/// confidence, a status, one sentence, and a box. Restated for a different
/// document on the server side and shared here, because a v8 invoice screen
/// and this one are the same moment over different ink and two shapes of it
/// would drift.
///
/// Two things are genuinely different from the invoice, and both are on
/// screen:
///
/// - **A fifth status, `out_of_band`.** It does not mean the reading is bad.
///   It means a *computed* figure — the hours a governed formula produced from
///   a grid — landed outside the range that department's own page states. The
///   card says exactly that, because a supervisor who reads it as "the
///   extraction misread something" will go looking on the page for a number
///   that was never printed.
/// - **The computed hours have no ink.** A path under `hours.` was never on
///   the document, so it never went to the locator and never gets a box. That
///   is not a locator failure and the screen must not draw it as one — see
///   [isComputedHours] and the note under the grids.
///
/// Three states, and none of them is the same as the others — the same three
/// `accounts_payable/v8/invoice_review.dart` documents, for the same reasons:
///
/// - **Fields in doubt.** The normal case, and what the layout is for.
/// - **`fields` empty.** The run computed verdicts and every one of them came
///   back `ok`. A fast approve, which has to say why it is in front of anybody
///   at all rather than showing an empty form that reads as a missing page.
/// - **`fields` null.** *Nobody looked.* A version that raised the Task without
///   a verdict step behind it. Not "nothing was wrong" — so everything is
///   shown, open, and the screen says it cannot narrow the question.
///
/// **Null and empty are different facts and this screen depends on it.**
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../../models/data_layer.dart';
import '../../../models/task.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/document_drawer.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';

/// The prefix a governed formula's output lives under — `hours.steel-fab`.
///
/// The server's own rule (`shipping_list_extraction/paths.py`), matched as a
/// prefix rather than as "a path containing `hours`". The looser test would
/// catch `ll_loader_list.board_hours`, which is a number *printed on the
/// loader list* — real ink, a real box, and a supervisor who should be sent to
/// the page to check it. Reading it as computed would tell them there is
/// nothing to look at.
const String hoursPrefix = 'hours.';

bool isComputedHours(String path) => path.startsWith(hoursPrefix);

/// The department a computed path names, for putting the figure beside the
/// right grid. Empty for anything that is not a computed path.
String departmentOf(String path) =>
    isComputedHours(path) ? path.substring(hoursPrefix.length) : '';

/// Worst first, in the flow's own order: an absent number is not "low
/// confidence", it is absent, and a figure outside its band is a bigger claim
/// than a smudge.
int severityOf(String status) => switch (status) {
  'missing' => 0,
  'out_of_band' => 1,
  'inconsistent' => 2,
  'low_confidence' => 3,
  _ => 4,
};

/// What the pill says, and how loudly. `out_of_band` is the one that has to
/// name the *computation* rather than the reading.
(String, CumulusTone) statusPill(String status) => switch (status) {
  'missing' => ('not found', CumulusTone.danger),
  'inconsistent' => ('does not add up', CumulusTone.danger),
  'out_of_band' => ('computed figure out of band', CumulusTone.warn),
  'low_confidence' => ('unsure', CumulusTone.warn),
  'ok' => ('clean', CumulusTone.success),
  '' => ('status not stated', CumulusTone.neutral),
  _ => (status, CumulusTone.neutral),
};

/// The `Departments with demand` evidence row, which is where the computed
/// hours actually live: `steel-fab 14 h, truss-fab 28.5 h`.
const String demandEvidenceLabel = 'Departments with demand';

/// One verdict with the input a person edits it through.
///
/// Every field gets its own controller here, unlike the invoice screen: there
/// is no second form on this page holding a draft of the same value, so there
/// is nothing for a shared controller to keep in step with.
class _Field {
  final FieldVerdict verdict;
  final TextEditingController input;

  _Field(this.verdict)
      : input = TextEditingController(text: verdict.value);

  String get path => verdict.path;

  bool get computed => isComputedHours(verdict.path);

  /// The key the box on the page is lit by — the region's own field rather
  /// than the path. The contract says they are the same vocabulary; if a run
  /// ever disagrees with itself the two-way selection should still work.
  String? get boxKey => verdict.region?.field;

  bool get changed => input.text.trim() != verdict.value.trim();

  void dispose() => input.dispose();
}

/// Fetches the page, then draws the review beside it.
class ExtractionReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const ExtractionReview({super.key, required this.api, required this.task});

  @override
  State<ExtractionReview> createState() => _ExtractionReviewState();
}

/// Below this the two panes stack, with the work above the page — the fields
/// are what the screen is for.
const double _twoPaneWidth = 880;

class _ExtractionReviewState extends State<ExtractionReview> {
  final _note = TextEditingController();
  final List<_Field> _fields = [];
  final List<_Field> _disputed = [];

  Future<(StoredDocument, Uint8List?)>? _artifact;

  /// The page being looked at, one-based.
  int _page = 1;

  /// The selected card's path, and the region field of the box it lights. Two
  /// keys rather than one: a verdict with no region is still selectable, and a
  /// box is still hoverable while its card is scrolled out of sight.
  String? _focus;
  String? _active;

  bool _showRest = false;

  /// Null when the run never computed verdicts. Held rather than re-read so
  /// the three states are decided once.
  late final List<FieldVerdict>? _verdicts = widget.task.proposal?.fields;

  @override
  void initState() {
    super.initState();
    _build();
  }

  @override
  void dispose() {
    for (final field in _fields) {
      field.dispose();
    }
    _note.dispose();
    super.dispose();
  }

  void _build() {
    _fields.addAll([for (final v in _verdicts ?? const []) _Field(v)]);

    // Sorted by arrival index as the tie-break, because `List.sort` is not
    // stable and two equally urgent fields swapping places between builds is
    // the kind of nothing that moves a card out from under a pointer.
    final flagged = [
      for (final field in _fields)
        if (field.verdict.disputed) field,
    ];
    final indexed = [for (var i = 0; i < flagged.length; i++) (i, flagged[i])]
      ..sort((a, b) {
        final bySeverity = severityOf(a.$2.verdict.status)
            .compareTo(severityOf(b.$2.verdict.status));
        return bySeverity != 0 ? bySeverity : a.$1.compareTo(b.$1);
      });
    _disputed.addAll([for (final entry in indexed) entry.$2]);

    // A run that reported nothing is a run nobody can trust to have looked, so
    // the whole reading is open rather than folded behind a chevron.
    _showRest = _verdicts == null;

    final first = _disputed.isEmpty ? null : _disputed.first;
    _focus = first?.path;
    _active = first?.boxKey;
    final page = first?.verdict.region?.page ?? 1;
    _page = page < 1 ? 1 : page;

    final documentId = _documentId;
    if (documentId.isNotEmpty) {
      _artifact = _fetchArtifact(widget.api, documentId);
    }
  }

  /// The artifact the run read, named on the Task's refs as `document:<id>`.
  String get _documentId {
    for (final ref in widget.task.refs) {
      if (ref.startsWith('document:')) return ref.substring(9);
    }
    return '';
  }

  /// The computed hours per department, as the flow wrote them in evidence.
  ///
  /// Read off the Task rather than recomputed: the hours are what the plant's
  /// own formulas produced at the revision named on the row, and a console
  /// that multiplied the numbers itself would be a second implementation of a
  /// governed formula.
  String get _demand {
    for (final row in widget.task.evidence) {
      if (row.label == demandEvidenceLabel) return row.value;
    }
    return '';
  }

  int get _settled => _fields.length - _disputed.length;

  bool get _dirty => _fields.any((field) => field.changed);

  /// Only paths the *run itself* named, and only those whose value moved.
  ///
  /// Two rules, both about the correction set being a clean raw→corrected
  /// pair: a key the screen invented could not be matched back against the
  /// reading, and a pair whose halves are equal is not a correction.
  Map<String, dynamic> _payload() {
    final corrections = <String, String>{};
    for (final field in _fields) {
      if (field.changed) corrections[field.path] = field.input.text.trim();
    }
    return corrections.isEmpty ? const {} : {'fields': corrections};
  }

  void _decide(String verdict) {
    Navigator.of(context).pop(ReviewOutcome(
      verdict: verdict,
      note: _note.text.trim(),
      payload: verdict == 'corrected' ? _payload() : const {},
    ));
  }

  /// One gesture, both selections: the card and its box on the page.
  void _select(_Field field) => setState(() {
    if (_focus == field.path) {
      _focus = null;
      _active = null;
      return;
    }
    _focus = field.path;
    _active = field.boxKey;
    final page = field.verdict.region?.page ?? _page;
    _page = page < 1 ? 1 : page;
  });

  /// The page asking for a field. Null — the pointer leaving a box — is
  /// ignored: a supervisor who moves off a box to type into its input has not
  /// stopped working on that field.
  void _selectBox(String? boxField) {
    if (boxField == null) return;
    setState(() {
      _active = boxField;
      for (final field in _disputed) {
        if (field.boxKey == boxField) {
          _focus = field.path;
          return;
        }
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return ReviewFrame(
      eyebrow: 'Shipping list · ${widget.task.queue}',
      title: widget.task.title,
      subtitle:
          'The values the run could not settle, in the check’s own words, '
          'boxed on the page beside. Correcting one re-runs the grid checks '
          'and the governed labor formulas over the corrected reading.',
      footer: DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: 'Apply and re-check',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxWidth < _twoPaneWidth) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                ..._workPane(),
                const SizedBox(height: 24),
                ..._pagePane(),
              ],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: _workPane(),
                ),
              ),
              const SizedBox(width: 26),
              SizedBox(
                width: 340,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: _pagePane(),
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  // -- the work pane --------------------------------------------------------

  List<Widget> _workPane() => [
    _lede(),
    const SizedBox(height: 16),
    ..._hoursSection(),
    if (_disputed.isNotEmpty) ...[
      const SizedBox(height: 14),
      for (var i = 0; i < _disputed.length; i++) ...[
        if (i > 0) const SizedBox(height: 10),
        _VerdictCard(
          field: _disputed[i],
          selected: _focus == _disputed[i].path,
          autofocus: i == 0,
          onSelect: () => _select(_disputed[i]),
          onChanged: () => setState(() {}),
        ),
      ],
    ],
    const SizedBox(height: 22),
    ..._restSection(),
  ];

  /// What the screen is asking, in one sentence, before it asks it.
  Widget _lede() {
    if (_verdicts == null) {
      return const _Lede(
        tone: CumulusTone.neutral,
        title: 'This run reported no field verdicts',
        body: 'The version that raised this task did not say which fields it '
            'was unsure of, so the screen cannot narrow the question — and '
            'that is a different fact from every field being fine. The whole '
            'reading is below, open, and correcting any of it re-runs the '
            'checks and the formulas.',
      );
    }
    if (_disputed.isEmpty) {
      return const _Lede(
        tone: CumulusTone.success,
        title: 'Nothing needed a person',
        body: 'The run computed a verdict for every field it read and all of '
            'them came back clean: the grids agree with their printed totals, '
            'every required field on a present page family was found, and '
            'every computed figure landed inside its department’s governed '
            'band. This is in front of you because the flow routes each '
            'ticket to a supervisor, not because anything is in doubt. '
            'Approving is the whole job.',
      );
    }
    final settled = _settled;
    return _Lede(
      tone: CumulusTone.warn,
      title: _disputed.length == 1
          ? 'One field needs you'
          : '${_disputed.length} fields need you',
      body: 'These are the values the run could not settle on its own, in the '
          'check’s own words, boxed on the page beside where there is ink to '
          'box. '
          '${settled > 0 ? "The other $settled field(s) it read came back "
              "clean and are folded below." : "Everything else it read is "
              "folded below."}',
    );
  }

  /// The computed hours, beside the grids that produced them.
  ///
  /// Its own block rather than a line in the lede, because the hours are the
  /// output of the whole document and the one figure that reaches the plant's
  /// day. A supervisor confirming a grid is really confirming these.
  List<Widget> _hoursSection() {
    final demand = _demand;
    final outOfBand = [
      for (final field in _disputed)
        if (field.verdict.status == 'out_of_band') field,
    ];
    if (demand.isEmpty && outOfBand.isEmpty) return const [];
    return [
      Container(
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
            const FieldLabel(
              'Computed hours',
              hint: 'what the plant’s own formulas make of the grids',
            ),
            Text(
              demand.isEmpty ? 'none stated on this task' : demand,
              style: CumulusText.body,
            ),
            const SizedBox(height: 8),
            Text(
              'These figures are not printed anywhere on the shipping list. A '
              'governed formula produced them from the grids, at the asset '
              'revision named on the row, so they carry no box on the page — '
              'the way to change one is to correct the grid it was computed '
              'from.',
              style: CumulusText.meta,
            ),
            if (outOfBand.isNotEmpty) ...[
              const SizedBox(height: 10),
              for (final field in outOfBand)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    '${departmentOf(field.path).isEmpty
                        ? field.verdict.displayLabel
                        : departmentOf(field.path)}: the computed figure is '
                    'outside this department’s governed band. The reading may '
                    'be perfectly legible and the hours still wrong.',
                    style: CumulusText.body.copyWith(
                      color: CumulusTone.warn.foreground,
                    ),
                  ),
                ),
            ],
          ],
        ),
      ),
    ];
  }

  /// Everything the run was sure about, folded away.
  List<Widget> _restSection() {
    final rest = [
      for (final field in _fields)
        if (!field.verdict.disputed) field,
    ];
    if (rest.isEmpty && _verdicts != null) {
      return [
        Text(
          _disputed.isEmpty
              ? 'The run reported no fields at all for this ticket, which is '
                  'not the same as a clean one — there is nothing here to '
                  'fold away and nothing to check.'
              : 'Every field the run reported is in doubt, so there is nothing '
                  'folded away underneath.',
          style: CumulusText.meta,
        ),
      ];
    }
    return [
      _Fold(
        label: 'The rest of the reading',
        caption: _verdicts == null
            ? 'the whole reading — nothing was singled out'
            : '${rest.length} field(s), none of them flagged',
        open: _showRest,
        onToggle: () => setState(() => _showRest = !_showRest),
        children: [
          for (final field in rest)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          field.verdict.displayLabel,
                          style: CumulusText.body,
                        ),
                        Text(field.path, style: CumulusText.mono),
                      ],
                    ),
                  ),
                  const SizedBox(width: 12),
                  SizedBox(
                    width: 180,
                    child: TextField(
                      controller: field.input,
                      onChanged: (_) => setState(() {}),
                      decoration: const InputDecoration(isDense: true),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    ];
  }

  // -- the page pane --------------------------------------------------------

  /// Only the disputed verdicts are boxed, and a computed path never is: the
  /// whole reason the locate call is cheap is that it runs on three fields,
  /// and three boxes on a page mean something where a hundred do not.
  List<DocumentRegion> get _boxes => [
    for (final field in _disputed)
      if (field.verdict.region != null) field.verdict.region!,
  ];

  List<Widget> _pagePane() {
    final artifact = _artifact;
    if (artifact == null) {
      return [
        const FieldLabel('The shipping list', hint: 'no artifact'),
        _muted(
          'This task names no document. Nothing was filed for the run to read, '
          'so there is no page to draw and no box to draw on it — the values '
          'beside are the whole of what is known.',
        ),
      ];
    }
    return [
      FutureBuilder<(StoredDocument, Uint8List?)>(
        future: artifact,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const FieldLabel(
                  'The shipping list',
                  hint: 'the artifact did not load',
                ),
                _muted(
                  'The reading is still decidable: what the run read is beside, '
                  'and correcting it does not need the page. ${snapshot.error}',
                ),
              ],
            );
          }
          if (!snapshot.hasData) {
            return const Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final (document, bytes) = snapshot.data!;
          final pages = document.pages < 1 ? 1 : document.pages;
          final page = _page.clamp(1, pages);
          final boxes = regionsOnPage(_boxes, page);
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FieldLabel(
                'The shipping list',
                hint: document.filename.isEmpty
                    ? document.id
                    : document.filename,
              ),
              if (pages > 1) ...[
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    for (var n = 1; n <= pages; n++)
                      LensChip(
                        label: 'PAGE $n',
                        tone: CumulusTone.accent,
                        selected: n == page,
                        onTap: () => setState(() => _page = n),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
              ],
              DocumentPageView(
                document: document,
                bytes: bytes,
                page: page,
                regions: boxes,
                active: _active,
                onActive: _selectBox,
                maxWidth: 340,
              ),
              const SizedBox(height: 10),
              Text(_boxNote(boxes.length), style: CumulusText.meta),
            ],
          );
        },
      ),
    ];
  }

  String _boxNote(int here) {
    if (_verdicts == null) {
      return 'This run raised no field verdicts, so nothing is boxed. The page '
          'is the artifact it read.';
    }
    if (_disputed.isEmpty) {
      return 'Nothing is boxed: no field was in doubt, so no field was located '
          '— the locate call is skipped entirely on a clean ticket.';
    }
    final boxed = _boxes.length;
    final computed = _disputed.where((field) => field.computed).length;
    if (boxed == 0) {
      return computed == _disputed.length
          ? 'Nothing is boxed, and nothing should be: every field in doubt is '
              'a computed figure, and a computed figure has no ink on the page.'
          : 'None of the ${_disputed.length} field(s) in doubt came back with '
              'a box. They are still editable beside; a missing box is the '
              'locator saying it could not find the value, not the value '
              'being fine.';
    }
    return '$here of $boxed box(es) on this page. A box is where the run '
        'looked, not the answer — the value beside it is what you are '
        'correcting.'
        '${computed > 0 ? " $computed field(s) in doubt are computed figures "
            "and are never boxed." : ""}';
  }

  Widget _muted(String message) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: CumulusColors.surfaceMuted,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Text(message, style: CumulusText.body),
  );
}

/// The invoice and its artifact as one settled future — the same pairing the
/// document drawer loads, and for the same reason: the metadata is worth
/// showing even when the bytes will not come.
Future<(StoredDocument, Uint8List?)> _fetchArtifact(
  ApiClient api,
  String documentId,
) async {
  final document = await api.getDocument(documentId);
  if (!document.hasBytes) return (document, null);
  try {
    return (document, await api.documentContent(documentId));
  } on ApiException {
    return (document, null);
  }
}

/// One field in doubt: what was read, why it is in doubt, and the input.
///
/// The check's sentence is shown verbatim — the run wrote it knowing what it
/// looked at, and a screen paraphrasing "the B truss grid's printed total is
/// 118 and its rows sum to 124" into "grid mismatch" has thrown away the
/// answer.
class _VerdictCard extends StatelessWidget {
  final _Field field;
  final bool selected;
  final bool autofocus;
  final VoidCallback onSelect;
  final VoidCallback onChanged;

  const _VerdictCard({
    required this.field,
    required this.selected,
    required this.autofocus,
    required this.onSelect,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final verdict = field.verdict;
    final (label, tone) = statusPill(verdict.status);
    return Material(
      color: selected ? CumulusColors.accentSoft : CumulusColors.surface,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      child: InkWell(
        onTap: onSelect,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        child: Container(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(
              color: selected
                  ? CumulusColors.accent
                  : CumulusColors.borderStrong,
              width: selected ? 1.6 : 1,
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      verdict.displayLabel,
                      style: CumulusText.rowTitle,
                    ),
                  ),
                  const SizedBox(width: 10),
                  StatusBadge(label, tone: tone, dense: true),
                ],
              ),
              const SizedBox(height: 6),
              Text(verdict.reason, style: CumulusText.body),
              if (field.computed) ...[
                const SizedBox(height: 6),
                Text(
                  'This is a computed figure, not a reading: a governed formula '
                  'produced it from the grids above. Nothing on the page says '
                  'it, so nothing on the page can be checked against it — what '
                  'is in question is whether the hours are right for this job, '
                  'and the way to move them is to correct the grid.',
                  style: CumulusText.meta,
                ),
              ],
              const SizedBox(height: 10),
              TextField(
                controller: field.input,
                autofocus: autofocus,
                onChanged: (_) => onChanged(),
                onTap: onSelect,
                decoration: const InputDecoration(
                  labelText: 'As it should read',
                  isDense: true,
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 12,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(
                    'read “${verdict.value.isEmpty ? "nothing" : verdict.value}”',
                    style: CumulusText.meta,
                  ),
                  Text(
                    verdict.confidence <= 0
                        ? 'no confidence reported'
                        : '${(verdict.confidence * 100).round()}% confident',
                    style: CumulusText.meta,
                  ),
                  Text(verdict.path, style: CumulusText.mono),
                  Text(
                    field.computed
                        ? 'computed — never boxed'
                        : verdict.region == null
                            ? 'no box on the page'
                            : 'boxed on page '
                                '${verdict.region!.page < 1
                                    ? 1
                                    : verdict.region!.page}',
                    style: CumulusText.meta.copyWith(
                      color: verdict.region == null
                          ? CumulusColors.inkMuted
                          : CumulusColors.accentInk,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// What the screen is asking, in one sentence, before it asks it.
class _Lede extends StatelessWidget {
  final CumulusTone tone;
  final String title;
  final String body;

  const _Lede({required this.tone, required this.title, required this.body});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: CumulusText.rowTitle.copyWith(color: tone.foreground),
          ),
          const SizedBox(height: 6),
          Text(body, style: CumulusText.body),
        ],
      ),
    );
  }
}

/// A disclosure in the console's own idiom: a chevron, a small-caps label, and
/// a caption saying what is behind it while it is shut.
///
/// Written here rather than imported: the invoice screen's is private to its
/// own file, and a widget lifted into `shared/` to serve two screens would be
/// a third place to keep in step for the sake of thirty lines.
class _Fold extends StatelessWidget {
  final String label;
  final String caption;
  final bool open;
  final VoidCallback onToggle;
  final List<Widget> children;

  const _Fold({
    required this.label,
    required this.caption,
    required this.open,
    required this.onToggle,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: onToggle,
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 3, horizontal: 2),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  open ? Icons.expand_less : Icons.expand_more,
                  size: 15,
                  color: CumulusColors.inkMuted,
                ),
                const SizedBox(width: 4),
                Text(label.toUpperCase(), style: CumulusText.statLabel),
                const SizedBox(width: 8),
                Flexible(
                  child: Text(
                    caption,
                    style: CumulusText.statCaption,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
        ),
        if (open) ...[const SizedBox(height: 10), ...children],
      ],
    );
  }
}
