/// The field-documents gap, as the person who can close it meets it.
///
/// The `field-documents` flow raises at most two Tasks per window, and neither
/// of them is a request to *do* something. The whole design of that flow is
/// that a window which finds eleven jobs with nothing filed asks one question
/// eleven times over, which is one question — so the Task is an aggregate, the
/// rows ride on it as evidence, and there is no proposal at all.
///
/// There is no proposal because **there is no Action behind either answer.**
/// Nothing in this system files a document on a foreman's behalf, and nothing
/// mints a crew nobody has hired. What a person supplies here is a *fact*: the
/// packet really is missing and somebody has been told, or this job's crew is
/// that team. The next window reads the fact and the finding goes away because
/// it is no longer true, not because a button cleared it. The screen says that
/// out loud rather than implying a filing cabinet it does not have — see
/// [_nothingHappens] and the verbatim `resumes` line, which is the server's own
/// account of the mechanism and is quoted rather than paraphrased.
///
/// Two classes, two shapes:
///
/// - **Nothing filed past the threshold.** The answer is an acknowledgement:
///   yes, this packet is genuinely absent and it is being chased — or no, this
///   row is not a gap and should not be counted as one. A toggle per row.
/// - **Unresolved crew.** The answer *names a Team*, which is an identity
///   question rather than a document one, so the row gets a picker over the
///   teams this deployment actually has rather than a free-text box that could
///   invent a crew.
///
/// A third case is deliberately handled: a class this build has never heard of.
/// The flow can grow a finding before the console ships a shape for it, and the
/// honest degrade is the acknowledge form — a person can still say "seen, and
/// here is what I know" over rows they can read, which is strictly better than
/// a screen that refuses to open.
library;

import 'package:flutter/material.dart';

import '../../../models/task.dart';
import '../../../models/team.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';

/// The finding whose answer names a Team.
const String unresolvedCrewClass = 'unresolved-crew';

/// The finding whose answer is an acknowledgement.
///
/// Two spellings, because the flow's own constant is
/// `no-documents-past-threshold` and the console has been asked for
/// `no-documents-filed` more than once. Matching both costs nothing and means
/// a rename on one side degrades to the same shape rather than to the fallback
/// — and the fallback *is* this shape, so even a spelling nobody anticipated
/// still lands somewhere a person can work.
const Set<String> noDocumentsClasses = {
  'no-documents-past-threshold',
  'no-documents-filed',
};

/// The evidence rows that describe the *finding* rather than a job.
///
/// The flow writes two or three of these before the per-job rows, and which
/// ones depend on the class. Named as a set rather than counted, because
/// "the first three" would silently promote a job row to a heading the day a
/// class stops carrying its `Threshold`.
const Set<String> _headerLabels = {
  'Finding',
  'Jobs',
  'Threshold',
  'Crews',
};

/// A job number as the source writes them: a run of at least four digits.
///
/// Loose on purpose. The evidence row's label is `job_label or job_number`,
/// which is usually `260118 Evermore` and is sometimes just the number, and
/// the screen has to key the answer by the number in both cases.
final RegExp _jobNumber = RegExp(r'\d{4,}');

/// The crew id the source printed, which the flow renders as ``crew `id` ``.
final RegExp _crewId = RegExp(r'crew `([^`]+)`');

/// The class this task's `caseRef` names.
///
/// `field-documents-gap:<class>`. Split on the last colon rather than the
/// first: the prefix is a fixed string but a class that ever grew one would
/// otherwise be read as a truncation of itself.
String gapClassOf(String caseRef) {
  final at = caseRef.lastIndexOf(':');
  if (at < 0 || at + 1 >= caseRef.length) return '';
  return caseRef.substring(at + 1);
}

/// One job on the aggregate, and the answer a person has given about it.
///
/// Holds both keys it could be answered under. [jobNumber] is what the crew
/// payload is keyed by — the plan says so, and the graph's job natural key is
/// the number — and [label] is what a person reads and what the acknowledge
/// payload carries. When the number cannot be parsed the row is *kept*, keyed
/// by the label, and says so on screen: dropping a job because the console
/// could not find a number in it would lose the one row somebody has to act
/// on, and an answer keyed by a label the flow can still recognise is far
/// better than no answer at all.
class _JobRow {
  final String label;
  final String detail;
  final String jobNumber;
  final String crewId;

  /// null = untouched, true = acknowledged, false = dismissed.
  bool? acknowledged;

  /// The chosen team's slug, or null while the row is unanswered.
  String? crewSlug;

  _JobRow({
    required this.label,
    required this.detail,
    required this.jobNumber,
    required this.crewId,
  });

  bool get keyedByNumber => jobNumber.isNotEmpty;

  /// What the crew answer is keyed by: the job number when there is one, and
  /// otherwise the label, which is the only other string the flow wrote.
  String get crewKey => keyedByNumber ? jobNumber : label;
}

class GapReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const GapReview({super.key, required this.api, required this.task});

  @override
  State<GapReview> createState() => _GapReviewState();
}

class _GapReviewState extends State<GapReview> {
  final _note = TextEditingController();
  final List<_JobRow> _rows = [];
  final List<TaskEvidence> _context = [];

  /// The teams, for the crew picker. Empty until the fetch lands, and empty
  /// after one that failed — [_teamsError] is what tells those apart, because
  /// "this deployment has no teams" and "the roster did not load" are opposite
  /// things to tell somebody about to answer an identity question.
  List<Team> _teams = const [];
  String? _teamsError;
  bool _teamsLoaded = false;

  late final String _class = gapClassOf(widget.task.caseRef);

  bool get _isCrewClass => _class == unresolvedCrewClass;

  /// Everything that is not the crew question takes the acknowledge shape,
  /// including a class this build does not recognise. See the library comment.
  bool get _isKnownClass =>
      _isCrewClass || noDocumentsClasses.contains(_class);

  @override
  void initState() {
    super.initState();
    _split();
    if (_isCrewClass) _loadTeams();
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  /// The evidence, cut into the finding's own description and the jobs.
  void _split() {
    for (final row in widget.task.evidence) {
      if (_headerLabels.contains(row.label)) {
        _context.add(row);
        continue;
      }
      final number = _jobNumber.stringMatch(row.label) ??
          _jobNumber.stringMatch(row.value) ??
          '';
      _rows.add(_JobRow(
        label: row.label,
        detail: row.value,
        jobNumber: number,
        crewId: _crewId.firstMatch(row.value)?.group(1) ?? '',
      ));
    }
  }

  Future<void> _loadTeams() async {
    setState(() => _teamsError = null);
    try {
      final teams = await widget.api.listTeams();
      if (!mounted) return;
      setState(() {
        _teams = teams;
        _teamsLoaded = true;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _teamsError = e.message;
          _teamsLoaded = true;
        });
      }
    }
  }

  bool get _dirty {
    for (final row in _rows) {
      if (_isCrewClass) {
        if (row.crewSlug != null) return true;
      } else if (row.acknowledged != null) {
        return true;
      }
    }
    return false;
  }

  /// The answers, in the shape the flow's next window reads.
  ///
  /// Only rows somebody actually answered. An untouched row is not a third
  /// verdict about the job — it is a person who did not get to it, and sending
  /// it as an empty acknowledgement would put a claim on the record that
  /// nobody made.
  Map<String, dynamic> _payload() {
    if (_isCrewClass) {
      final crew = <String, String>{};
      for (final row in _rows) {
        final slug = row.crewSlug;
        if (slug != null && slug.isNotEmpty) crew[row.crewKey] = slug;
      }
      return crew.isEmpty ? const {} : {'crew': crew};
    }
    final acknowledged = <String>[];
    final dismissed = <String>[];
    for (final row in _rows) {
      switch (row.acknowledged) {
        case true:
          acknowledged.add(row.label);
        case false:
          dismissed.add(row.label);
        case null:
          break;
      }
    }
    return {
      if (acknowledged.isNotEmpty) 'acknowledged': acknowledged,
      if (dismissed.isNotEmpty) 'dismissed': dismissed,
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
    return ReviewFrame(
      eyebrow: 'Field documents · ${widget.task.queue}',
      title: widget.task.title,
      subtitle: _isCrewClass
          ? 'Name the crew each job belongs to. Nothing here creates a team — '
              'the answer is a fact about who is on the job, and the next '
              'window reads it.'
          : 'Say which of these gaps are real. Nothing here files a document — '
              'the packet closes when somebody files one, and the next window '
              'sees it.',
      footer: DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: _isCrewClass ? 'Record these crews' : 'Record these answers',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (widget.task.summary.isNotEmpty) ...[
            Text(widget.task.summary, style: CumulusText.body),
            const SizedBox(height: 18),
          ],
          _nothingHappens(),
          const SizedBox(height: 18),
          if (_context.isNotEmpty) ...[
            const FieldLabel(
              'What the window found',
              hint: 'the finding, before the jobs it found it on',
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
          if (!_isKnownClass) ...[
            _UnknownClass(name: _class),
            const SizedBox(height: 16),
          ],
          FieldLabel(
            _isCrewClass ? 'The jobs and their crews' : 'The jobs',
            hint: '${_rows.length} row(s) — one question, asked once',
          ),
          if (_rows.isEmpty)
            Text(
              'The Task carries no job rows. The finding above is the whole of '
              'what it says, and there is nothing here to answer row by row.',
              style: CumulusText.body,
            ),
          if (_isCrewClass) ..._crewRows() else ..._acknowledgeRows(),
          const SizedBox(height: 20),
          _resumes(),
        ],
      ),
    );
  }

  /// The claim the screen must not overstate, stated first.
  Widget _nothingHappens() => Container(
    width: double.infinity,
    padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
    decoration: BoxDecoration(
      color: CumulusTone.neutral.background,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('NOTHING HERE FILES OR CREATES ANYTHING',
            style: CumulusText.statLabel),
        const SizedBox(height: 7),
        Text(
          _isCrewClass
              ? 'This screen does not create a crew, hire anybody, or change '
                  'who works where. It records which team the job actually '
                  'belongs to — a fact you supply, sourced to you — and the '
                  'next window resolves the job against it. If the right crew '
                  'genuinely does not exist yet, the answer is to create the '
                  'team, not to pick the nearest one.'
              : 'This screen does not file a document, create a folder, or '
                  'chase anybody. It records whether each of these gaps is '
                  'real. The packet closes when a document is filed against '
                  'the job, and the next window reads the filing rather than '
                  'this answer.',
          style: CumulusText.body,
        ),
      ],
    ),
  );

  /// The server's own sentence about what happens next, verbatim.
  ///
  /// Quoted rather than restated because the screen is in no position to
  /// promise a mechanism: the flow wrote this knowing what it will do with the
  /// decision, and a console paraphrase is how a screen ends up promising a
  /// resume that does not exist.
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

  // -- the acknowledge shape ------------------------------------------------

  List<Widget> _acknowledgeRows() => [
    for (final row in _rows)
      Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: _RowCard(
          label: row.label,
          detail: row.detail,
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              LensChip(
                label: 'REAL GAP',
                tone: CumulusTone.warn,
                selected: row.acknowledged == true,
                onTap: () => setState(() {
                  row.acknowledged = row.acknowledged == true ? null : true;
                }),
              ),
              const SizedBox(width: 8),
              LensChip(
                label: 'NOT A GAP',
                tone: CumulusTone.success,
                selected: row.acknowledged == false,
                onTap: () => setState(() {
                  row.acknowledged = row.acknowledged == false ? null : false;
                }),
              ),
            ],
          ),
        ),
      ),
  ];

  // -- the crew shape -------------------------------------------------------

  List<Widget> _crewRows() {
    if (!_teamsLoaded) {
      return const [
        Padding(
          padding: EdgeInsets.symmetric(vertical: 30),
          child: Center(
            child: SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(strokeWidth: 2.2),
            ),
          ),
        ),
      ];
    }
    return [
      if (_teamsError != null) ...[
        ErrorNotice(
          message:
              'The team roster did not load, so there is nothing to pick from: '
              '${_teamsError!}',
          onRetry: _loadTeams,
        ),
        const SizedBox(height: 12),
      ] else if (_teams.isEmpty) ...[
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: CumulusColors.surfaceMuted,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
          ),
          child: Text(
            'This deployment has no teams. That is not a loading failure — it '
            'is the answer, and it means the crews these jobs name have not '
            'been created yet. Creating them is the work; nothing on this '
            'screen can stand in for it.',
            style: CumulusText.body,
          ),
        ),
        const SizedBox(height: 12),
      ],
      for (final row in _rows)
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: _RowCard(
            label: row.label,
            detail: row.detail,
            // The one place the screen admits it could not read the row. Said
            // beside the picker rather than in a banner, because it changes
            // what *this* answer is keyed by and nothing else on the page.
            warning: row.keyedByNumber
                ? null
                : 'No job number could be read off this row, so an answer here '
                    'is keyed by the label above rather than by the number.',
            trailing: SizedBox(
              width: 260,
              child: DropdownButtonFormField<String>(
                initialValue: row.crewSlug,
                isDense: true,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText: 'Crew',
                  hintText: row.crewId.isEmpty
                      ? 'which team is on this job'
                      : 'source says `${row.crewId}`',
                  isDense: true,
                ),
                items: [
                  for (final team in _teams)
                    DropdownMenuItem(
                      value: team.slug,
                      child: Text(
                        team.name.isEmpty ? team.slug : team.name,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: _teams.isEmpty
                    ? null
                    : (value) => setState(() => row.crewSlug = value),
              ),
            ),
          ),
        ),
    ];
  }
}

/// One job, its detail line as the flow wrote it, and whatever answers it.
class _RowCard extends StatelessWidget {
  final String label;
  final String detail;
  final Widget trailing;
  final String? warning;

  const _RowCard({
    required this.label,
    required this.detail,
    required this.trailing,
    this.warning,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 13),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        border: Border.all(color: CumulusColors.border),
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
                    Text(label, style: CumulusText.rowTitle),
                    if (detail.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(detail, style: CumulusText.meta),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 14),
              trailing,
            ],
          ),
          if (warning != null) ...[
            const SizedBox(height: 8),
            Text(
              warning!,
              style: CumulusText.meta.copyWith(color: CumulusColors.warn),
            ),
          ],
        ],
      ),
    );
  }
}

/// A finding this build has no shape for.
///
/// Shown rather than swallowed. The person answering is entitled to know that
/// the console is offering them a generic form because it does not recognise
/// the class, not because the class is a generic one.
class _UnknownClass extends StatelessWidget {
  final String name;

  const _UnknownClass({required this.name});

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
          Text('A FINDING THIS CONSOLE DOES NOT KNOW',
              style: CumulusText.statLabel),
          const SizedBox(height: 7),
          Text(
            'The flow raised “${name.isEmpty ? "an unnamed class" : name}”, '
            'which this build has no purpose-made form for — the deployment is '
            'ahead of the console. The rows are below as the flow wrote them '
            'and you can still acknowledge or dismiss each one, which is the '
            'part of the answer that does not depend on knowing the class.',
            style: CumulusText.body,
          ),
        ],
      ),
    );
  }
}
