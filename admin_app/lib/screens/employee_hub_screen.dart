import 'dart:convert';

import 'package:desktop_drop/desktop_drop.dart';
import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../models/employee.dart';
import '../models/graph.dart';
import '../services/api_client.dart';
import '../tasks/expense_reimbursement/models.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/hr_agent_panel.dart';
import '../widgets/skeleton.dart';
import '../widgets/ui_kit.dart';

/// The Employee Hub — the one-stop personal page: PTO balance and requests,
/// benefits, expense submission, and the HR Agent.
///
/// A section inside the shell rather than a pushed route, so the rail, the
/// command palette, and the task-queue badge stay live while you are in it —
/// asking the HR Agent about your PTO should not mean leaving the console.
/// It is entered from the account menu rather than the nav rail because it is
/// about *you*, not about a workstream, which is also why the rail shows no
/// selection here and the account button carries the active ring instead.
///
/// Everything here runs on the governed machinery underneath: a PTO request
/// over the policy window raises a Task to the HR queue; a submitted receipt
/// runs the live expense-reimbursement flow; the HR Agent answers from the
/// people/ taxonomy of the knowledge layer with citations.
class EmployeeHubSection extends StatefulWidget {
  /// The shell's client — the shell owns its lifecycle, so this section
  /// never closes it.
  final ApiClient api;

  /// Land on a document in the Library. The HR Agent's citations are the
  /// reason this exists: a citation you cannot open is a claim you cannot
  /// check, which is most of what a citation is for.
  final void Function(String assetId)? onOpenAsset;

  const EmployeeHubSection({super.key, required this.api, this.onOpenAsset});

  @override
  State<EmployeeHubSection> createState() => _EmployeeHubSectionState();
}

/// One read's outcome: what came back, or what went wrong reading it.
///
/// The hub is four independent reads behind one page and it used to treat them
/// as one: the first failure blanked the lot, so a dead PTO endpoint took the
/// receipts, the benefits and the HR Agent down with it. A result per read is
/// what lets a failure cost its own card and nothing else. Only the profile is
/// still load-bearing for the whole page — it is the page's identity, and
/// without it there is nobody to draw a hub for.
typedef _Read<T> = ({T? value, String? error});

class _EmployeeHubSectionState extends State<EmployeeHubSection> {
  ExpenseRollup? _rollup;
  ApiClient get _api => widget.api;
  EmployeeProfile? _profile;
  List<PtoRequest>? _pto;
  List<Reimbursement>? _mine;

  /// The cold-start failure: no profile, so there is no page to draw. Every
  /// other failure is a notice beside data that still stands.
  String? _error;

  /// A refresh that could not re-read the profile, over data already on the
  /// screen. Kept apart from [_error] because replacing a page of good rows
  /// with an error card is the most destructive thing a Refresh button can do.
  String? _staleNotice;
  String? _ptoError;
  String? _expenseError;
  String? _rollupError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// Runs one read to a value or a message, never to a thrown error.
  ///
  /// Broad on purpose: the failure that used to hang this screen forever was
  /// not an `ApiException` at all but a `TypeError` out of a `fromJson` on a
  /// shape the server had changed — an unhandled async error, no `_error` set,
  /// and a spinner that never stopped.
  Future<_Read<T>> _read<T>(Future<T> future) async {
    try {
      return (value: await future, error: null);
    } on ApiException catch (e) {
      return (value: null, error: e.message);
    } catch (e) {
      return (value: null, error: '$e');
    }
  }

  Future<void> _load() async {
    // Three independent reads: the profile does not gate the PTO list or the
    // receipts, and running them in series spent three round trips of latency
    // to draw one page. Started together, awaited in turn.
    final profileRead = _read(_api.employeeProfile());
    final ptoRead = _read(_api.listPtoRequests());
    final mineRead = _read(_api.listReimbursements(mine: true));
    final profile = await profileRead;
    final pto = await ptoRead;
    final mine = await mineRead;

    // The traversal, not a search: Employee --submitted--> ExpenseRequest
    // --has_expense_line--> ExpenseLine, summed. It is the question the
    // graph exists to make answerable, and until now nothing asked it. It
    // needs the resolved address, so it is the one read that waits.
    _Read<ExpenseRollup> rollup = (value: null, error: null);
    final resolved = profile.value;
    if (resolved != null) {
      rollup = await _read(_api.employeeExpenses(resolved.email));
    }

    if (!mounted) return;
    setState(() {
      // Whatever came back replaces what it had; whatever did not keeps it.
      // A failed refresh leaves the page as it was and says so.
      if (profile.value != null) _profile = profile.value;
      if (pto.value != null) _pto = pto.value;
      if (mine.value != null) _mine = mine.value;
      if (rollup.value != null) _rollup = rollup.value;
      _ptoError = pto.error;
      _expenseError = mine.error;
      // A principal outside the org chart has nothing to traverse from, which
      // is an answer rather than a fault — so the rollup only reports a
      // failure to somebody it was supposed to have one for.
      _rollupError = (resolved?.inWorkforce ?? false) ? rollup.error : null;
      // Only the read nothing else can stand without blanks the page, and only
      // while there is nothing to stand on.
      _error = _profile == null ? profile.error : null;
      _staleNotice = _profile == null ? null : profile.error;
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow: 'Employee hub · yours',
            // A principal outside the org chart has no name to show, so the
            // heading falls back to the address rather than rendering blank.
            title: _hubTitle(),
            subtitle: _hubSubtitle(),
            trailing: OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
        ),
        Expanded(child: _body()),
      ],
    );
  }

  Widget _body() {
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load your hub',
        message: _error!,
      );
    }
    if (_profile == null) return const _HubSkeleton();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // The Refresh that failed over a page that had already loaded. Said
        // here rather than by swapping the page for an error card: what is on
        // screen is still the last good read, and it is still worth reading.
        if (_staleNotice != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(28, 14, 28, 0),
            child: ErrorNotice(
              message:
                  'Could not refresh — showing the last good read. '
                  '$_staleNotice',
              onRetry: _load,
            ),
          ),
        Expanded(child: _columns()),
      ],
    );
  }

  Widget _columns() {
    return LayoutBuilder(
      builder: (context, constraints) {
        // The rail and the section padding eat into the width the standalone
        // page had, so the two-column split turns on a little earlier.
        final wide = constraints.maxWidth >= 1000;
        final left = _leftColumn();
        final chat = HrAgentPanel(
          ask: (message, history) => _api.hrChat(message, history: history),
          inWorkforce: _profile!.inWorkforce,
          onOpenAsset: widget.onOpenAsset,
        );
        if (!wide) {
          return SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(28, 16, 28, 40),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                left,
                const SizedBox(height: 16),
                SizedBox(height: 560, child: chat),
              ],
            ),
          );
        }
        return Padding(
          padding: const EdgeInsets.fromLTRB(28, 16, 28, 24),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(flex: 3, child: SingleChildScrollView(child: left)),
              const SizedBox(width: 20),
              Expanded(flex: 2, child: chat),
            ],
          ),
        );
      },
    );
  }

  String _hubTitle() {
    final profile = _profile;
    if (profile == null) return 'Your hub';
    if (profile.name.isNotEmpty) return profile.name;
    return profile.email.isEmpty ? 'Your hub' : profile.email;
  }

  /// What this page actually offers *this* reader.
  ///
  /// The old copy promised time off, expenses and benefits to everyone and
  /// said the agent answered "from the people policies", which was both a
  /// promise the operator's view could not keep and a description of a scope
  /// the agent has stopped having — it reads the whole corpus now, because
  /// travel and expenses are employee questions filed under other desks.
  String _hubSubtitle() {
    final profile = _profile;
    if (profile != null && !profile.inWorkforce) {
      return 'This account is not on the customer\u2019s org chart, so the '
          'personal half does not apply. The HR Agent still answers policy '
          'questions from the governed corpus, with citations.';
    }
    return 'Time off, expenses, and benefits \u2014 plus the HR Agent, which '
        'answers from the governed corpus and your own records, with '
        'citations and the precedence it ranked them under.';
  }

  Widget _leftColumn() {
    final profile = _profile!;
    // A principal the org chart has never heard of — the operator, the support
    // engineer — has no balance, no enrolment and no manager. The page used to
    // render the whole personal column anyway: two stat tiles reading `0` and
    // an empty one, a form that would deny whatever it submitted, and a
    // Benefits card with nothing in it. That is not an empty state, it is a
    // broken one, and it was the first thing anybody saw here.
    //
    // So the personal half is not drawn at all, and what stands in its place
    // says why and names what *does* apply to this account. The governed rule
    // stays, because the PTO window is a fact about the deployment rather than
    // about the reader.
    if (!profile.inWorkforce) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _WhoThisIs(profile: profile),
          const SizedBox(height: 14),
          _GovernedRuleCard(
            autoApproveDays: profile.ptoAutoApproveDays,
            policyRef: profile.ptoPolicyRef,
          ),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _WhoThisIs(profile: profile),
        if (_rollup != null) ...[
          const SizedBox(height: 14),
          _SpendRollup(rollup: _rollup!),
        ] else if (_rollupError != null) ...[
          const SizedBox(height: 14),
          CumulusCard(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
              child: _CardNotice(
                'The spend walk did not come back, so this card is empty '
                'rather than wrong. $_rollupError',
              ),
            ),
          ),
        ],
        const SizedBox(height: 14),
        StatStrip(
          tiles: [
            // A balance nobody delivered is not a balance. This tile used to
            // read `17.5` with a caption admitting it was "the deployment
            // default, not an HRIS balance" — a number and a disclaimer, where
            // the honest rendering of an absent fact is an em dash.
            StatTile(
              label: 'PTO balance',
              value: profile.ptoBalanceDays == null
                  ? '\u2014'
                  : '${profile.ptoBalanceDays}',
              caption: profile.ptoBalanceDays == null
                  ? 'no balance on record'
                  : 'days'
                        '${profile.ptoAccrualPerMonth == null ? '' : ' · +${profile.ptoAccrualPerMonth}/month'}',
            ),
            // A failed read is not zero pending requests. `0` under "waiting
            // on HR" is the one wrong answer this tile can give, so it
            // declines to give it.
            StatTile(
              label: 'Pending requests',
              value: _ptoError != null
                  ? '—'
                  : '${(_pto ?? []).where((r) => r.state == 'pending').length}',
              caption: _ptoError != null ? 'not read' : 'with HR',
            ),
            // Drawn only when a calendar has one. An empty tile captioned with
            // half of an empty string was the third thing on this screen that
            // said nothing while looking like data.
            if (profile.nextCompanyHoliday.isNotEmpty)
              StatTile(
                label: 'Next holiday',
                value: profile.nextCompanyHoliday.split('·').last.trim(),
                caption: profile.nextCompanyHoliday.split('·').first.trim(),
              ),
          ],
        ),
        if (profile.recordsNote.isNotEmpty) ...[
          const SizedBox(height: 14),
          CumulusCard(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
              child: _CardNotice(profile.recordsNote),
            ),
          ),
        ],
        const SizedBox(height: 16),
        _PtoCard(
          api: _api,
          onChanged: _load,
          requests: _pto ?? const [],
          // The form still works when the history behind it did not load:
          // requesting time off does not depend on being able to list what you
          // asked for last month.
          notice: _ptoError == null
              ? null
              : 'Your request history did not load, so the list below may be '
                    'short. $_ptoError',
          autoApproveDays: profile.ptoAutoApproveDays,
          policyRef: profile.ptoPolicyRef,
        ),
        const SizedBox(height: 16),
        _ExpenseCard(
          api: _api,
          onChanged: _load,
          mine: _mine ?? const [],
          notice: _expenseError == null
              ? null
              : 'Your recent requests did not load. Submitting still works. '
                    '$_expenseError',
        ),
        const SizedBox(height: 16),
        // Every profile used to get the same four invented plans — "Health PPO
        // 2500", a 4% match, a learning stipend — minted on first read and shown
        // as this person's enrolments. Nothing delivers enrolment data, so the
        // card says that instead. What the organization *offers* is written
        // down in the governed benefits guide, which the HR Agent cites.
        SectionCard(
          title: 'Benefits',
          subtitle: profile.benefits.isEmpty
              ? 'no enrolment is on record for you'
              : 'as delivered for you',
          child: Column(
            children: [
              if (profile.benefits.isEmpty)
                const _CardNotice(
                  'Enrolment is not modelled yet: no system delivers which '
                  'plans you are in, so this card shows nothing rather than a '
                  'set of plans everybody would see. Ask the HR Agent what the '
                  'organization offers — it answers from the governed benefits '
                  'guide, with the citation.',
                ),
              for (final b in profile.benefits)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Monogram(b.kind.substring(0, 1).toUpperCase()),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Text(b.name, style: CumulusText.rowTitle),
                                const SizedBox(width: 8),
                                StatusBadge(
                                  b.status,
                                  tone: CumulusTone.success,
                                  dense: true,
                                ),
                              ],
                            ),
                            const SizedBox(height: 2),
                            Text(b.summary, style: CumulusText.body),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

/// One card's own bad news, stated inside that card.
///
/// Quieter than [ErrorNotice], which owns a whole page's failure. This one says
/// "this part did not load" beside the parts that did — which is the honest
/// reading when three independent reads back one screen and one of them fell
/// over.
class _CardNotice extends StatelessWidget {
  final String message;

  const _CardNotice(this.message);

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: CumulusColors.warnSoft,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(
            Icons.info_outline,
            size: 14,
            color: CumulusColors.warn,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: CumulusText.meta.copyWith(color: CumulusColors.warn),
            ),
          ),
        ],
      ),
    );
  }
}

/// The hub's own shape while its reads are in flight.
///
/// The page heading, the eyebrow and the Refresh button above this are real
/// and already on screen; only the regions waiting on data are grey. The
/// geometry mirrors [_leftColumn] and the agent panel beside it — profile
/// card, stat strip, the two form cards, the chat frame — so nothing moves
/// when the reads land.
class _HubSkeleton extends StatelessWidget {
  const _HubSkeleton();

  @override
  Widget build(BuildContext context) {
    const left = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SkeletonCard(
          padding: EdgeInsets.fromLTRB(16, 16, 16, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              SkeletonBar(width: 190, height: 15),
              SizedBox(height: 9),
              SkeletonBar(width: 320, height: 10),
            ],
          ),
        ),
        SizedBox(height: 14),
        SkeletonStatStrip(count: 3),
        SizedBox(height: 16),
        SkeletonCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              SkeletonBar(width: 96, height: 11),
              SizedBox(height: 10),
              SkeletonBar(width: 280, height: 9),
              SizedBox(height: 18),
              Row(
                children: [
                  SkeletonPill(width: 128),
                  SizedBox(width: 10),
                  Expanded(child: SkeletonBar(height: 20)),
                  SizedBox(width: 10),
                  SkeletonPill(width: 84),
                ],
              ),
              SizedBox(height: 18),
              SkeletonBar(width: 120, height: 9),
              SizedBox(height: 12),
              SkeletonBar(height: 11),
              SizedBox(height: 10),
              SkeletonBar(height: 11),
            ],
          ),
        ),
        SizedBox(height: 16),
        SkeletonCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              SkeletonBar(width: 140, height: 11),
              SizedBox(height: 10),
              SkeletonBar(width: 300, height: 9),
              SizedBox(height: 18),
              SkeletonBar(height: 96, radius: CumulusRadius.control),
              SizedBox(height: 16),
              SkeletonBar(height: 58, radius: CumulusRadius.control),
            ],
          ),
        ),
      ],
    );
    const chat = SkeletonCard(
      padding: EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              SkeletonCircle(size: 30),
              SizedBox(width: 10),
              SkeletonBar(width: 92, height: 12),
            ],
          ),
          SizedBox(height: 22),
          SkeletonBar(width: 260, height: 44, radius: 14),
          SizedBox(height: 12),
          Align(
            alignment: Alignment.centerRight,
            child: SkeletonBar(width: 170, height: 32, radius: 14),
          ),
          Spacer(),
          SkeletonBar(height: 34, radius: CumulusRadius.control),
        ],
      ),
    );
    return SkeletonPulse(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth >= 1000;
          if (!wide) {
            return const SingleChildScrollView(
              padding: EdgeInsets.fromLTRB(28, 16, 28, 40),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  left,
                  SizedBox(height: 16),
                  SizedBox(height: 560, child: chat),
                ],
              ),
            );
          }
          return const Padding(
            padding: EdgeInsets.fromLTRB(28, 16, 28, 24),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Expanded(
                  flex: 3,
                  child: SingleChildScrollView(child: left),
                ),
                SizedBox(width: 20),
                Expanded(flex: 2, child: chat),
              ],
            ),
          );
        },
      ),
    );
  }
}

// ---- PTO ---------------------------------------------------------------

class _PtoCard extends StatefulWidget {
  final ApiClient api;
  final VoidCallback onChanged;
  final List<PtoRequest> requests;

  /// The governed auto-approval window, and the ref it was parsed from. Served
  /// with the profile: this card claimed the window was governed while
  /// printing a literal `3`, so editing the policy asset moved the rule the
  /// flow applies and left the sentence beside it saying the old number.
  final double autoApproveDays;
  final String policyRef;

  /// What went wrong reading the history, when something did. The card renders
  /// either way: a form you can still use is worth more than a page that
  /// refused to draw because one list behind it was unavailable.
  final String? notice;

  const _PtoCard({
    required this.api,
    required this.onChanged,
    required this.requests,
    required this.autoApproveDays,
    required this.policyRef,
    this.notice,
  });

  @override
  State<_PtoCard> createState() => _PtoCardState();
}

class _PtoCardState extends State<_PtoCard> {
  DateTimeRange? _range;
  final _note = TextEditingController();
  bool _busy = false;
  String? _message;

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  double get _days {
    final range = _range;
    if (range == null) return 0;
    // Working days, weekend-aware — the honest count for a PTO request.
    var days = 0;
    for (var d = range.start;
        !d.isAfter(range.end);
        d = d.add(const Duration(days: 1))) {
      if (d.weekday <= 5) days++;
    }
    return days.toDouble();
  }

  Future<void> _pickRange() async {
    final now = DateTime.now();
    final picked = await showDateRangePicker(
      context: context,
      firstDate: now,
      lastDate: now.add(const Duration(days: 365)),
      initialDateRange: _range,
    );
    if (picked != null) setState(() => _range = picked);
  }

  Future<void> _submit() async {
    final range = _range;
    if (range == null) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    String iso(DateTime d) => d.toIso8601String().substring(0, 10);
    try {
      final request = await widget.api.requestPto(
        startDate: iso(range.start),
        endDate: iso(range.end),
        days: _days,
        note: _note.text.trim(),
      );
      setState(() {
        _busy = false;
        _range = null;
        _note.clear();
        _message = switch (request.state) {
          'approved' => 'Approved — ${request.reason}.',
          'pending' => 'Sent to HR — ${request.reason}.',
          _ => 'Declined — ${request.reason}.',
        };
      });
      widget.onChanged();
    } on ApiException catch (e) {
      setState(() {
        _busy = false;
        _message = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    // The number and the ref both come from the asset the flow reads, so the
    // rule this card states and the rule `request_pto` applies cannot drift.
    final window = widget.autoApproveDays;
    return SectionCard(
      title: 'Time off',
      subtitle: window <= 0
          ? 'the PTO policy does not state an auto-approval window, so every '
                'request routes to HR'
          : 'requests of ${window.toStringAsFixed(window % 1 == 0 ? 0 : 1)} '
                'working days or fewer auto-approve — governed by '
                '${widget.policyRef.isEmpty ? 'the PTO policy' : widget.policyRef}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (widget.notice != null) ...[
            _CardNotice(widget.notice!),
            const SizedBox(height: 12),
          ],
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: _busy ? null : _pickRange,
                icon: const Icon(Icons.event, size: 16),
                label: Text(
                  _range == null
                      ? 'Pick dates'
                      : '${_range!.start.toIso8601String().substring(0, 10)} → '
                            '${_range!.end.toIso8601String().substring(0, 10)}'
                            ' · ${_days.toStringAsFixed(0)}d',
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: TextField(
                  controller: _note,
                  enabled: !_busy,
                  decoration: const InputDecoration(
                    labelText: 'Note (coverage, reason)',
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              FilledButton(
                onPressed: _busy || _range == null ? null : _submit,
                child: _busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Request'),
              ),
            ],
          ),
          if (_message != null) ...[
            const SizedBox(height: 10),
            Text(_message!, style: CumulusText.body),
          ],
          if (widget.requests.isNotEmpty) ...[
            const SizedBox(height: 14),
            const FieldLabel('Your requests'),
            for (final r in widget.requests.take(5))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    StatusBadge(
                      r.state,
                      tone: switch (r.state) {
                        'approved' => CumulusTone.success,
                        'pending' => CumulusTone.warn,
                        _ => CumulusTone.danger,
                      },
                      dense: true,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      '${r.startDate} → ${r.endDate} · ${r.days.toStringAsFixed(0)}d',
                      style: CumulusText.body,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        r.reason,
                        style: CumulusText.meta,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

// ---- Expenses ------------------------------------------------------------

/// The expense submission form.
///
/// A submission is **N documents plus the submitter's own words**, and both
/// halves are load-bearing:
///
/// - Several receipts go in as one request, because a trip is one request. Each
///   carries its own note, because "only claiming $75 of the $110 — the rest
///   was mine" is about *that* receipt.
/// - The overall commentary is a governed input to the run: the projection
///   reads a stated claim out of a note, both are quoted in the run journal,
///   and a stated purpose is what keeps a line above the governed threshold
///   from needing a reviewer at all.
///
/// So the form's job is to make the notes feel worth writing rather than
/// optional. The hint text says what they do.
class _ExpenseCard extends StatefulWidget {
  final ApiClient api;
  final VoidCallback onChanged;
  final List<Reimbursement> mine;

  /// What went wrong reading `mine`, when something did. Submitting a receipt
  /// does not depend on being able to list the last five.
  final String? notice;

  const _ExpenseCard({
    required this.api,
    required this.onChanged,
    required this.mine,
    this.notice,
  });

  @override
  State<_ExpenseCard> createState() => _ExpenseCardState();
}

/// One attachment being composed, with its note.
class _Attachment {
  final XFile? file;
  final String name;
  final TextEditingController note = TextEditingController();

  _Attachment({this.file, required this.name});

  void dispose() => note.dispose();
}

class _ExpenseCardState extends State<_ExpenseCard> {
  final List<_Attachment> _attachments = [];
  final _commentary = TextEditingController();
  bool _busy = false;
  bool _dragging = false;
  String? _message;
  Reimbursement? _result;

  @override
  void dispose() {
    for (final attachment in _attachments) {
      attachment.dispose();
    }
    _commentary.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final files = await openFiles(
      acceptedTypeGroups: const [
        XTypeGroup(
          label: 'receipts',
          extensions: ['png', 'jpg', 'jpeg', 'pdf'],
        ),
      ],
    );
    if (files.isEmpty) return;
    setState(() {
      for (final file in files) {
        _attachments.add(_Attachment(file: file, name: file.name));
      }
    });
  }

  void _addFromDrop(List<XFile> files) {
    setState(() {
      _dragging = false;
      for (final file in files) {
        _attachments.add(_Attachment(file: file, name: file.name));
      }
    });
  }

  void _remove(_Attachment attachment) {
    setState(() {
      _attachments.remove(attachment);
      attachment.dispose();
    });
  }

  /// Evidence is the requirement, and prose is not evidence.
  ///
  /// The button used to light up on commentary alone, and the door it posts
  /// through now refuses that with a 400 — "this door carries bytes, not a
  /// story". A submission described in prose used to be accepted, extracted,
  /// projected into lines and written to the graph, leaving facts with no
  /// artifact behind them, indistinguishable afterwards from facts with one.
  /// An enabled button over a request the server will reject is the console
  /// disagreeing with its own backend in the one place a person can see it.
  bool get _canSubmit => !_busy && _attachments.isNotEmpty;

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _message = null;
      _result = null;
    });
    try {
      final documents = <Map<String, String>>[];
      for (final attachment in _attachments) {
        final bytes = await attachment.file?.readAsBytes();
        documents.add({
          'content_base64': bytes == null ? '' : base64Encode(bytes),
          'filename': attachment.name,
          'note': attachment.note.text.trim(),
        });
      }
      final row = await widget.api.submitReimbursement(
        documents: documents,
        commentary: _commentary.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _busy = false;
        for (final attachment in _attachments) {
          attachment.dispose();
        }
        _attachments.clear();
        _commentary.clear();
        _result = row;
        _message = switch (row.state) {
          'approved' =>
            'Approved automatically — \$${row.requestedUsd.toStringAsFixed(2)} '
                'across ${row.lines.length} line(s). ${row.verdictReason}.',
          // Not "elevated for review", and not a named queue. Under the live
          // version every run stops and the review comes back to the person
          // who submitted it — nobody else can say whether the number on the
          // receipt is the number on the receipt — so this is not an
          // escalation and there is no clerk waiting on it. The desk is
          // deliberately unnamed: the governed routing table sends it to your
          // own, and only a routing that could not open one falls back to the
          // shared expense-review desk. The console cannot tell which
          // happened from this response, and My Tasks shows either.
          _ =>
            'Submitted — the run read your receipt'
                '${row.documents.length == 1 ? '' : 's'} and stopped for you '
                'to confirm what it read. Nothing has been recorded yet: open '
                'My Tasks to check the values and confirm them.'
                '${row.escalations.isEmpty ? '' : ' The governed check also '
                    'had something to say: ${row.verdictReason}.'}',
        };
      });
      widget.onChanged();
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _message = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return SectionCard(
      title: 'Submit an expense',
      subtitle:
          'several receipts go in as one request · a note on a receipt is read '
          'by the flow and shown back to you, and is never given to the '
          'extractor',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (widget.notice != null) ...[
            _CardNotice(widget.notice!),
            const SizedBox(height: 12),
          ],
          DropTarget(
            onDragEntered: (_) => setState(() => _dragging = true),
            onDragExited: (_) => setState(() => _dragging = false),
            onDragDone: (detail) => _addFromDrop(detail.files),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 120),
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 22, horizontal: 18),
              decoration: BoxDecoration(
                color: _dragging
                    ? CumulusColors.accentSoft
                    : CumulusColors.surfaceMuted.withValues(alpha: 0.6),
                borderRadius: BorderRadius.circular(CumulusRadius.control),
                border: Border.all(
                  color: _dragging
                      ? CumulusColors.accent
                      : CumulusColors.borderStrong,
                  width: _dragging ? 1.6 : 1,
                ),
              ),
              child: Column(
                children: [
                  Icon(
                    Icons.cloud_upload_outlined,
                    size: 22,
                    color: _dragging
                        ? CumulusColors.accent
                        : CumulusColors.inkMuted,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _dragging
                        ? 'Drop them here'
                        : 'Drag receipts here, or add them',
                    style: const TextStyle(
                      fontSize: 13.5,
                      fontWeight: FontWeight.w600,
                      color: CumulusColors.ink,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'PNG, JPG or PDF · one request can carry several',
                    style: CumulusText.meta,
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    onPressed: _busy ? null : _pick,
                    icon: const Icon(Icons.add, size: 16),
                    label: const Text('Add receipts'),
                  ),
                ],
              ),
            ),
          ),
          if (_attachments.isNotEmpty) ...[
            const SizedBox(height: 14),
            const FieldLabel(
              'Attached receipts',
              hint:
                  'a note here can say what a receipt was for, or that you are '
                  'only claiming part of it',
            ),
            for (final attachment in _attachments)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Padding(
                      padding: EdgeInsets.only(top: 10),
                      child: Icon(
                        Icons.receipt_outlined,
                        size: 16,
                        color: CumulusColors.inkMuted,
                      ),
                    ),
                    const SizedBox(width: 8),
                    SizedBox(
                      width: 180,
                      child: Padding(
                        padding: const EdgeInsets.only(top: 9),
                        child: Text(
                          attachment.name,
                          style: CumulusText.body,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: attachment.note,
                        enabled: !_busy,
                        decoration: const InputDecoration(
                          hintText:
                              'e.g. dinner with the Acme team after the Phase 2 '
                              'review — only claiming \$75 of the \$110',
                          isDense: true,
                        ),
                      ),
                    ),
                    IconButton(
                      onPressed: _busy ? null : () => _remove(attachment),
                      icon: const Icon(Icons.close, size: 16),
                      tooltip: 'Remove',
                    ),
                  ],
                ),
              ),
          ],
          const SizedBox(height: 12),
          LabeledField(
            label: 'What was this for?',
            hint:
                'read by the flow — a stated purpose is what keeps a larger '
                'claim from needing a reviewer',
            child: TextField(
              controller: _commentary,
              enabled: !_busy,
              maxLines: 3,
              minLines: 2,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                hintText:
                    'Client visit to Acme — cab, lunch, and the team dinner '
                    'after the Phase 2 review.',
                isDense: true,
              ),
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              FilledButton.icon(
                onPressed: _canSubmit ? _submit : null,
                icon: _busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.send, size: 16),
                label: Text(
                  _attachments.length > 1
                      ? 'Submit ${_attachments.length} receipts'
                      : 'Submit',
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  _attachments.isEmpty
                      // The server's own sentence, because the button is
                      // disabled for exactly the reason the door refuses.
                      ? 'Attach at least one receipt — this door carries '
                          'bytes, not a story.'
                      : 'Submitting runs the flow now: one metered extraction '
                          'per receipt, the governed policy read, then a '
                          'confirmation that comes back to you. Nothing is '
                          'recorded until you confirm it.',
                  style: CumulusText.meta,
                ),
              ),
            ],
          ),
          if (_message != null) ...[
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              decoration: BoxDecoration(
                color: (_result?.state == 'approved'
                        ? CumulusTone.success
                        : CumulusTone.warn)
                    .background,
                borderRadius: BorderRadius.circular(CumulusRadius.control),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(_message!, style: CumulusText.body),
                  if (_result != null && _result!.lines.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    for (final line in _result!.lines)
                      Text(
                        '· line ${line.ordinal}: ${line.vendor} · '
                        '${line.category} · claiming '
                        '\$${line.requestedUsd.toStringAsFixed(2)}'
                        '${line.isSplit ? ' of \$${line.documentTotalUsd.toStringAsFixed(2)} on the receipt' : ''}',
                        style: CumulusText.meta,
                      ),
                  ],
                  if (_result != null && _result!.runId.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(_result!.runId, style: CumulusText.mono),
                  ],
                ],
              ),
            ),
          ],
          if (widget.mine.isNotEmpty) ...[
            const SizedBox(height: 16),
            const FieldLabel('Your recent requests'),
            for (final r in widget.mine.take(5))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    StatusBadge(
                      r.state,
                      tone: switch (r.state) {
                        'approved' => CumulusTone.success,
                        'pending' => CumulusTone.warn,
                        _ => CumulusTone.danger,
                      },
                      dense: true,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      '${r.vendor} · \$${r.requestedUsd.toStringAsFixed(2)}'
                      '${r.lines.length > 1 ? ' · ${r.lines.length} lines' : ''}',
                      style: CumulusText.body,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        r.verdictReason,
                        style: CumulusText.meta,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

/// The governed PTO rule on its own, for a reader with no records to apply it
/// to. The window is a fact about the deployment rather than about the person,
/// so it is the one thing the personal column can honestly still show.
class _GovernedRuleCard extends StatelessWidget {
  final double autoApproveDays;
  final String policyRef;

  const _GovernedRuleCard({
    required this.autoApproveDays,
    required this.policyRef,
  });

  @override
  Widget build(BuildContext context) {
    return SectionCard(
      title: 'The governed rule',
      subtitle: 'what this deployment does with a time-off request, whoever '
          'makes it',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            autoApproveDays <= 0
                ? 'The PTO policy does not state an auto-approval window, so '
                      'every request routes to the HR queue.'
                : 'Requests of '
                      '${autoApproveDays.toStringAsFixed(autoApproveDays % 1 == 0 ? 0 : 1)} '
                      'working days or fewer auto-approve when the balance '
                      'covers them; longer ones raise a Task to the HR queue.',
            style: CumulusText.body,
          ),
          const SizedBox(height: 10),
          MetaPill(policyRef.isEmpty ? 'knowledge:pto-policy' : policyRef),
        ],
      ),
    );
  }
}


/// Who the hub is showing, read through from the org chart.
///
/// The hub used to mint a blank profile for whoever asked, so it never had a
/// person to name. Now it resolves an `Employee` by email, and a principal the
/// org chart has never heard of — Naviant Support is the standing case — says
/// so in a sentence rather than rendering an empty page that reads as broken.
class _WhoThisIs extends StatelessWidget {
  final EmployeeProfile profile;

  const _WhoThisIs({required this.profile});

  @override
  Widget build(BuildContext context) {
    if (!profile.inWorkforce) {
      return CumulusCard(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            profile.workforceNote.isEmpty
                ? 'This account is not in the workforce, so there is no '
                      'balance, manager or enrolment to show.'
                : profile.workforceNote,
            style: CumulusText.meta,
          ),
        ),
      );
    }
    final line = [
      profile.title,
      profile.team,
      if (profile.manager.isNotEmpty) 'reports to ${profile.manager}',
      profile.location,
    ].where((s) => s.isNotEmpty).join('  ·  ');
    return CumulusCard(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(profile.name, style: CumulusText.sectionTitle),
            const SizedBox(height: 4),
            Text(line, style: CumulusText.meta),
          ],
        ),
      ),
    );
  }
}


/// What this person has spent, and the path the answer came down.
///
/// A two-hop walk from a resolved Employee rather than a search over rows —
/// which is the difference the graph exists to make, so the traversal is shown
/// rather than hidden. The endpoint has existed since the entity layer landed
/// and had no caller until now.
class _SpendRollup extends StatelessWidget {
  final ExpenseRollup rollup;

  const _SpendRollup({required this.rollup});

  @override
  Widget build(BuildContext context) {
    final byCategory = rollup.byCategory.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    return CumulusCard(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Your spend · last ${rollup.windowDays} days',
              style: CumulusText.sectionTitle,
            ),
            const SizedBox(height: 4),
            Text(
              '\$${rollup.approvedUsd.toStringAsFixed(2)} approved across '
              '${rollup.requests} request(s) and ${rollup.lines} line(s)',
              style: CumulusText.body,
            ),
            if (byCategory.isNotEmpty) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final e in byCategory)
                    MetaPill(
                      '${e.key} \$${e.value.toStringAsFixed(2)}',
                    ),
                ],
              ),
            ],
            const SizedBox(height: 10),
            Text(
              rollup.traversal.join('  '),
              style: CumulusText.mono.copyWith(
                fontSize: 11,
                color: CumulusColors.inkSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
