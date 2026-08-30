import 'package:flutter/material.dart';

import '../models/identity_screen.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart' show PageBody;
import 'merge_review.dart';
import 'ui_kit.dart';

/// Counterparty identity — the curation desk for the book itself.
///
/// **Why a Workspace and not a lens on the Ontology.** The person who owns
/// this work is the Controller, and he is own-scoped: he reads his own work
/// and both master-data desks are his. Every stop in the governance group of
/// the rail — the Knowledge layer, the identity roster — is dropped for a
/// persona like that, and correctly so, because those screens describe the
/// whole map. This one does not describe anything; it *is* work, with a queue
/// behind it and a Task per decision, exactly like Accounts Payable. So it
/// takes the shape the console already has for that: a Workspace, a list of
/// what is outstanding, and a pane that holds the whole argument for one row.
/// A lens on the Ontology would have put a steward's worklist inside a
/// governance surface he cannot open.
///
/// The layout is My Tasks', for the same reason: deciding one of these means
/// reading a table, following the evidence, and opening the governed policy —
/// which needs a pane, not a drawer.
class IdentityWorkbench extends StatefulWidget {
  final ApiClient api;

  /// Fired after a decision lands, so the shell's task badge can follow it.
  final VoidCallback? onQueueChanged;

  /// A proposal to land on, by case ref — how an entity's detail panel sends
  /// you here. Null lands on the first standing proposal.
  final String? initialCaseRef;

  const IdentityWorkbench({
    super.key,
    required this.api,
    this.onQueueChanged,
    this.initialCaseRef,
  });

  @override
  State<IdentityWorkbench> createState() => _IdentityWorkbenchState();
}

class _IdentityWorkbenchState extends State<IdentityWorkbench> {
  IdentityScreen? _screen;
  String? _error;
  int? _refusedWith;
  bool _showDecided = false;
  String _book = '';
  String? _selected;
  bool _busy = false;
  String? _decisionError;

  @override
  void initState() {
    super.initState();
    _selected = widget.initialCaseRef;
    _load();
  }

  Future<void> _load() async {
    try {
      final screen = await widget.api.identityScreen();
      if (!mounted) return;
      setState(() {
        _screen = screen;
        _error = null;
        _refusedWith = null;
        // Follow the selection across a decision rather than dropping it. A
        // verdict moves a proposal from Standing to Decided, and clearing the
        // pane would bounce the reader off the row they just answered — with
        // the result of their own decision in it.
        final held = _selected == null ? null : screen.byCaseRef(_selected!);
        if (held == null) {
          _selected = null;
        } else {
          _showDecided = !held.isStanding;
        }
        _selected ??= _visible.firstOrNull?.caseRef;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
          _refusedWith = e.statusCode;
        });
      }
    }
  }

  List<IdentityProposal> get _visible {
    final screen = _screen;
    if (screen == null) return const [];
    final band = _showDecided ? screen.decided : screen.standing;
    if (_book.isEmpty) return band;
    return band
        .where((p) => p.left.type == _book || p.right.type == _book)
        .toList();
  }

  Future<void> _decide(String taskId, String verdict, String note) async {
    setState(() {
      _busy = true;
      _decisionError = null;
    });
    try {
      await widget.api.decideTask(taskId, verdict: verdict, note: note);
      widget.onQueueChanged?.call();
      if (!mounted) return;
      setState(() => _busy = false);
      await _load();
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _decisionError = e.statusCode == 409
            ? 'Someone already decided this proposal.'
            : e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final screen = _screen;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow: screen == null
                ? 'Master data'
                : 'Master data · ${screen.curates ? screen.desks.join(" · ") : "read-only"}',
            title: 'Counterparty identity',
            subtitle:
                'Two records, one real-world party — or two parties that '
                'merely look alike. The sweep proposes on evidence you can '
                'read; a person decides. Nothing is ever collapsed: a '
                'confirmed proposal is an assertion both records carry, and it '
                'can be withdrawn.',
            pills: [
              if (screen != null && screen.standing.isNotEmpty)
                MetaPill(
                  '${screen.standing.length} standing',
                  tone: CumulusTone.warn,
                ),
              if (screen != null && screen.decidedTotal > 0)
                MetaPill('${screen.decidedTotal} decided'),
              if (screen != null && !screen.curates)
                const MetaPill('observer', tone: CumulusTone.neutral),
            ],
            trailing: OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
        ),
        const SizedBox(height: 16),
        Expanded(child: _body(screen)),
      ],
    );
  }

  Widget _body(IdentityScreen? screen) {
    final error = _error;
    if (error != null) {
      // A refusal is not an outage, so it gets no Retry: pressing it again
      // would refuse again, and a button that can only fail is what makes an
      // access boundary read like a bug.
      return _refusedWith == 403
          ? CumulusEmptyState(
              icon: Icons.lock_outline,
              title: 'This is not scoped to you',
              message: error,
            )
          : CumulusEmptyState(
              icon: Icons.cloud_off,
              title: 'Could not read the counterparty book',
              message: error,
              action: OutlinedButton(
                onPressed: _load,
                child: const Text('Try again'),
              ),
            );
    }
    if (screen == null) return const CumulusLoading();
    if (screen.lastSweep == null && screen.standing.isEmpty) {
      return _NeverSwept(screen: screen);
    }

    final selected = _selected == null ? null : screen.byCaseRef(_selected!);
    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= 1080;
        final list = _list(screen);
        if (!wide) return list;
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(width: 420, child: list),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(6, 0, 28, 24),
                child: selected == null
                    ? const CumulusEmptyState(
                        icon: Icons.merge_outlined,
                        title: 'Nothing selected',
                        message:
                            'Pick a proposal to read the two records, the '
                            'evidence, and the governed policy behind the '
                            'score.',
                      )
                    : _pane(screen, selected),
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _pane(IdentityScreen screen, IdentityProposal proposal) {
    // Controls only where a decision has somewhere to go: an open Task in a
    // queue this persona holds. Everything else renders the gate's sentence.
    final decidable = proposal.isStanding &&
        proposal.gate.hasTask &&
        proposal.gate.isOpen &&
        proposal.gate.mayDecide;
    return CumulusCard(
      key: ValueKey(proposal.caseRef),
      padding: EdgeInsets.zero,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(24, 22, 24, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(proposal.pair, style: CumulusText.pageTitle),
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                StatusBadge(
                  proposal.pairKind,
                  tone: CumulusTone.teal,
                  dense: true,
                ),
                if (proposal.gate.hasTask)
                  MetaPill(proposal.gate.queue)
                else
                  // Not a policy any more — every proposal raises a Task — so
                  // this reads as the missing thing it is.
                  const MetaPill('no task carries this'),
                if (proposal.runId.isNotEmpty)
                  MetaPill('sweep ${proposal.runId}'),
              ],
            ),
            const SizedBox(height: 18),
            MergeReviewPanel(
              proposal: proposal,
              api: widget.api,
              busy: _busy,
              error: _decisionError,
              onDecide: decidable
                  ? (verdict, note) =>
                        _decide(proposal.gate.taskId, verdict, note)
                  : null,
            ),
          ],
        ),
      ),
    );
  }

  Widget _list(IdentityScreen screen) {
    final visible = _visible;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 0, 14, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SegmentedTabs(
                labels: [
                  'Standing · ${screen.standing.length}',
                  'Decided · ${screen.decidedTotal}',
                ],
                selectedIndex: _showDecided ? 1 : 0,
                onChanged: (i) => setState(() {
                  _showDecided = i == 1;
                  _book = '';
                  _selected = _visible.firstOrNull?.caseRef;
                }),
              ),
              const SizedBox(height: 12),
              _Books(
                books: screen.books,
                selected: _book,
                onTap: (type) => setState(() {
                  _book = _book == type ? '' : type;
                  _selected = _visible.firstOrNull?.caseRef;
                }),
              ),
              if (_showDecided && screen.decidedTotal > screen.decided.length)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'Showing the ${screen.decided.length} most recent of '
                    '${screen.decidedTotal}.',
                    style: CumulusText.meta,
                  ),
                ),
            ],
          ),
        ),
        Expanded(
          child: visible.isEmpty
              ? CumulusEmptyState(
                  icon: _showDecided ? Icons.history : Icons.done_all,
                  title: _showDecided
                      ? 'Nothing has been ruled on'
                      : 'No proposals outstanding',
                  message: _showDecided
                      ? 'Confirmed and withdrawn proposals stay here — that a '
                            'machine proposed a merge and a person answered is '
                            'the record worth keeping.'
                      : 'The last sweep compared '
                            '${screen.lastSweep?.compared ?? 0} pair(s) and '
                            'found nothing above the '
                            '${screen.policy.proposeAt.toStringAsFixed(2)} '
                            'floor that is still open.',
                )
              : ListView.builder(
                  padding: const EdgeInsets.fromLTRB(28, 0, 14, 32),
                  itemCount: visible.length,
                  itemBuilder: (context, i) => Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: _ProposalRow(
                      proposal: visible[i],
                      selected: visible[i].caseRef == _selected,
                      onTap: () =>
                          setState(() => _selected = visible[i].caseRef),
                    ),
                  ),
                ),
        ),
        if (screen.lastSweep != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(28, 0, 14, 14),
            child: _SweepFooter(
              sweep: screen.lastSweep!,
              policy: screen.policy,
            ),
          ),
      ],
    );
  }
}

/// Per-book counts, as filters. Every number here is answered by rows in the
/// list under it — the server counts them from the same proposals it serves.
class _Books extends StatelessWidget {
  final List<IdentityBook> books;
  final String selected;
  final ValueChanged<String> onTap;

  const _Books({
    required this.books,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final book in books)
          Tooltip(
            message: book.yours
                ? '${book.objects} ${book.label.toLowerCase()} record(s); '
                      'judged by ${book.stewardQueue}, which is yours'
                : '${book.objects} ${book.label.toLowerCase()} record(s); '
                      'judged by ${book.stewardQueue}',
            child: LensChip(
              label: '${book.label} · ${book.standing}',
              tone: book.yours ? CumulusTone.accent : CumulusTone.neutral,
              selected: selected == book.type,
              onTap: () => onTap(book.type),
            ),
          ),
      ],
    );
  }
}

class _ProposalRow extends StatelessWidget {
  final IdentityProposal proposal;
  final bool selected;
  final VoidCallback onTap;

  const _ProposalRow({
    required this.proposal,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final p = proposal;
    return CumulusCard(
      onTap: onTap,
      padding: const EdgeInsets.fromLTRB(16, 14, 14, 14),
      borderColor: selected ? CumulusColors.accent : null,
      borderWidth: selected ? 1.4 : 1,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              StatusBadge(
                switch (p.status) {
                  'confirmed' => 'confirmed',
                  'withdrawn' => 'withdrawn',
                  // Every proposal asks a steward now, so the second branch is
                  // a proposal with nowhere to be decided — the state this
                  // list should shout about rather than file under "standing".
                  _ => p.gate.hasTask ? 'asks a steward' : 'no decision path',
                },
                tone: switch (p.status) {
                  'confirmed' => CumulusTone.success,
                  'withdrawn' => CumulusTone.neutral,
                  _ => p.gate.hasTask ? CumulusTone.warn : CumulusTone.danger,
                },
                dense: true,
              ),
              const Spacer(),
              Text(p.score, style: CumulusText.mono.copyWith(fontSize: 12)),
            ],
          ),
          const SizedBox(height: 10),
          Text(p.pair, style: CumulusText.rowTitle),
          const SizedBox(height: 6),
          Text(
            p.conflict.isEmpty
                ? '${p.agree.length} field(s) agree'
                : '${p.conflict.length} field(s) disagree · '
                      '${p.agree.length} agree',
            style: CumulusText.statLabel,
          ),
          if (p.signals.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(
              p.signals.first.detail,
              style: CumulusText.meta,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ],
        ],
      ),
    );
  }
}

/// What the last pass did, and where the governed lines fall.
class _SweepFooter extends StatelessWidget {
  final SweepRun sweep;
  final SweepPolicy policy;

  const _SweepFooter({required this.sweep, required this.policy});

  @override
  Widget build(BuildContext context) {
    final when = DateTime.tryParse(sweep.atIso);
    return Text(
      'Last sweep ${when == null ? sweep.atIso : agoLabel(when.toLocal())}: '
      '${sweep.compared} pair(s) compared, ${sweep.considered} with any '
      'evidence, ${sweep.proposed} above the '
      '${policy.proposeAt.toStringAsFixed(2)} floor. Every one of them raised '
      'a Task — ${sweep.stewardTasks} in all, ${sweep.ratifications} of those '
      'a ratification at or above '
      '${policy.ratifyAt.toStringAsFixed(2)} rather than a question. '
      '${policy.foundAsset ? "Thresholds from ${policy.sourceRef}." : "The policy asset is missing — these are code defaults, not governed values."}',
      style: CumulusText.meta,
    );
  }
}

/// The empty state that is a statement rather than a blank list.
class _NeverSwept extends StatelessWidget {
  final IdentityScreen screen;

  const _NeverSwept({required this.screen});

  @override
  Widget build(BuildContext context) {
    final book = screen.books.fold<int>(0, (sum, b) => sum + b.objects);
    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 4, 28, 40),
      children: [
        CumulusCard(
          child: CumulusEmptyState(
            icon: Icons.radar_outlined,
            title: 'The counterparty book has never been swept',
            message: book == 0
                ? 'Nothing has landed in it yet, so there is nothing to '
                      'compare. Connect a source and the books fill; the sweep '
                      'runs over what is there, not over what is arriving.'
                : 'It holds $book record(s) across '
                      '${screen.books.map((b) => b.label.toLowerCase()).join(", ")}, '
                      'and no pass has scored them against each other. The '
                      'sweep is a windowed flow started by an event — '
                      '`POST /api/v1/identity/sweep` closes a window — and '
                      'until one closes, this screen has nothing to show '
                      'rather than nothing to find.',
          ),
        ),
      ],
    );
  }
}
